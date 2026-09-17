#!/usr/bin/env python3
"""보행 도로망·녹지 사전적재 — Geofabrik OSM → PostGIS `osm_way` (2026-09-11, 절차서 A).

왜: dog/course 의 15~50초는 전부 공개 Overpass 미러(504·빈 결과) 대기였다. 도로는 안 움직인다 →
전국 pbf 를 한 번 받아 보행 way·녹지 폴리곤만 DB에 넣고, roadnet.bbox() 가 DB를 먼저 읽는다.

서버 절차(호스트):
  sudo apt-get install -y osmium-tool
  wget -O ~/data/south-korea-latest.osm.pbf https://download.geofabrik.de/asia/south-korea-latest.osm.pbf
  osmium tags-filter -o ~/data/kr_walk.osm.pbf ~/data/south-korea-latest.osm.pbf \
    w/highway=footway,path,pedestrian,steps,living_street,residential,service,unclassified,tertiary,tertiary_link,secondary,secondary_link,primary,primary_link,track,cycleway \
    w/leisure=park,garden,playground,recreation_ground,nature_reserve \
    w/landuse=grass,forest,meadow,recreation_ground,cemetery,orchard,village_green,reservoir \
    w/natural=water,wood,scrub,grassland,heath,wetland,beach
  osmium export -f geojsonseq --add-unique-id=type_id -o ~/data/kr_walk.geojsonl ~/data/kr_walk.osm.pbf
  docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml run --rm \
    -v $HOME/climax_mvp:/repo -v $HOME/data:/data api python3 /repo/scripts/load_osm_ways.py /data/kr_walk.geojsonl [--replace]
일본 전국 (2026-09-16): 중간 geojsonl 을 만들면 WAS 디스크(여유 약 10 GB)가 먼저 찬다.
  지방별 pbf 를 하나씩 받아 **osmium 출력을 바로 표준입력으로 흘려 넣고**, 끝나면 pbf 를 지운다.
  그러면 피크 용량이 pbf 한 개(최대 약 1 GB)로 줄어 디스크를 늘리지 않고 전국이 된다.

  for R in kanto kansai chubu kyushu tohoku chugoku shikoku hokkaido; do
    wget -q -O ~/data/r.osm.pbf https://download.geofabrik.de/asia/japan/$R-latest.osm.pbf || continue
    osmium tags-filter -o ~/data/rw.osm.pbf ~/data/r.osm.pbf w/highway=… w/leisure=… w/landuse=… w/natural=…
    rm -f ~/data/r.osm.pbf
    osmium export -f geojsonseq --add-unique-id=type_id -o - ~/data/rw.osm.pbf \
      | docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml run --rm -i \
        -v $HOME/climax_mvp:/repo api python3 /repo/scripts/load_osm_ways.py -
    rm -f ~/data/rw.osm.pbf
  done

  ⚠️ `--replace` 는 쓰지 말 것 — 테이블이 하나라 한국 도로가 지워진다. id 충돌은 UPSERT 로 처리된다.
"""
from __future__ import annotations
import argparse, asyncio, json, sys, time
sys.path.insert(0, "/app")

DDL = """
CREATE TABLE IF NOT EXISTS osm_way (
    id      BIGINT PRIMARY KEY,
    tags    JSONB NOT NULL,
    geom    geometry(Geometry, 4326) NOT NULL,
    loaded  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_osm_way_geom ON osm_way USING GIST (geom);
"""
# `bicycle`/`cycleway` 추가 (2026-09-16). 자전거 모드가 이 두 태그로 통행 가능 여부
# (`_bike_ok` — 계단 제외)와 길 종류 표시(`_bike_class`)를 정한다. 여기서 안 담으면
# DB 경로로 오는 일본 도로는 자전거 태그가 통째로 비어, 계단이 경로에 섞인다.
# `layer`/`level`/`indoor` 추가 (2026-09-18). 도쿄·오사카 지하가와 역 구내 자유통로는
# 여름에 일본인이 실제로 쓰는 가장 시원한 길인데, 이 세 태그가 없으면 지상 보도와 구분되지 않아
# 경로가 뙤약볕 길을 골랐다. 지하는 직달·확산 일사가 0 이다 — 그늘(15%)보다도 시원하다.
KEEP = ("highway", "leisure", "landuse", "natural", "surface", "covered", "tunnel", "tree_lined",
        "area", "access", "foot", "name", "sidewalk", "lit", "width", "bridge", "steps", "incline",
        "bicycle", "cycleway", "layer", "level", "indoor")


def _wkt(geom: dict) -> str | None:
    t, c = geom.get("type"), geom.get("coordinates") or []
    def ring(r):
        return ",".join(f"{x:.6f} {y:.6f}" for x, y in r)
    if t == "LineString" and len(c) >= 2:
        return f"LINESTRING({ring(c)})"
    if t == "Polygon" and c and len(c[0]) >= 4:
        return f"POLYGON(({ring(c[0])}))"
    if t == "MultiPolygon":
        parts = [f"(({ring(p[0])}))" for p in c if p and len(p[0]) >= 4]
        return f"MULTIPOLYGON({','.join(parts)})" if parts else None
    return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojsonl", help="geojsonseq 파일 경로. '-' 면 표준입력(osmium export 에서 바로 받음)")
    ap.add_argument("--replace", action="store_true", help="기존 osm_way 비우고 적재")
    ap.add_argument("--batch", type=int, default=5000)
    a = ap.parse_args()
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 접속 실패"); return
    async with pool.acquire() as c:
        for stmt in filter(None, (x.strip() for x in DDL.split(";"))):
            await c.execute(stmt)
        if a.replace:
            await c.execute("TRUNCATE osm_way")
    t0 = time.time(); n = skip = 0; buf = []
    sql = ("INSERT INTO osm_way (id, tags, geom) VALUES ($1, $2::jsonb, ST_GeomFromText($3, 4326)) "
           "ON CONFLICT (id) DO UPDATE SET tags=EXCLUDED.tags, geom=EXCLUDED.geom, loaded=NOW()")

    async def flush():
        nonlocal buf
        if not buf:
            return
        async with pool.acquire() as c:
            await c.executemany(sql, buf)
        buf = []

    # 표준입력 지원 (2026-09-16). 일본 전국을 하려면 중간 geojsonl 이 문제다 —
    # 간토만 해도 수 GB 라 WAS 디스크(여유 10 GB)가 먼저 찬다. osmium 이 뱉는 대로
    # 바로 읽어 넣으면 그 파일이 아예 생기지 않고, 피크 용량이 pbf 하나로 줄어든다.
    #   osmium export -f geojsonseq --add-unique-id=type_id -o - r.osm.pbf \
    #     | docker ... run --rm -i ... python3 /repo/scripts/load_osm_ways.py -
    f = sys.stdin if a.geojsonl == "-" else open(a.geojsonl, encoding="utf-8")
    try:
        for line in f:
                line = line.strip().lstrip("\x1e")          # RS 구분자(geojsonseq)
                if not line.startswith("{"):
                    continue
                ft = json.loads(line)
                p = ft.get("properties") or {}
                # osmium export --add-unique-id=type_id 는 Feature 최상위 "id":"w123" (properties 아님)
                oid = str(ft.get("id") or p.get("@id") or p.get("id") or "")
                if not oid.startswith("w"):                  # way 만 (relation 다중폴리곤은 r… — 녹지 대형 공원용으로 음수 id로 넣는다)
                    if oid.startswith("r") and oid[1:].isdigit():
                        wid = -int(oid[1:])
                    else:
                        skip += 1; continue
                else:
                    wid = int(oid[1:])
                tags = {k: p[k] for k in KEEP if k in p}
                if not any(k in tags for k in ("highway", "leisure", "landuse", "natural")):
                    skip += 1; continue
                wkt = _wkt(ft.get("geometry") or {})
                if not wkt:
                    skip += 1; continue
                buf.append((wid, json.dumps(tags, ensure_ascii=False), wkt)); n += 1
                if len(buf) >= a.batch:
                    await flush()
                    if n % 100000 == 0:
                        print(f"  {n:,} ({time.time()-t0:.0f}s)", flush=True)
    finally:
        if f is not sys.stdin:
            f.close()
    await flush()
    async with pool.acquire() as c:
        tot = await c.fetchval("SELECT COUNT(*) FROM osm_way")
        await c.execute("ANALYZE osm_way")
    print(f"적재 {n:,} (건너뜀 {skip:,}) → osm_way 총 {tot:,}행  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
