#!/usr/bin/env python3
"""OSM 건물(geojsonseq) → PostGIS `bldg_poly` 직적재 — 해외용 (2026-09-12).

한국은 V-World(`fetch_vworld_tiles.py --to-db`), 해외는 OSM. 넣는 곳과 형식은 동일하므로
`geo._rings_from_db` 가 그대로 읽는다. 파일 타일을 거치지 않는 이유: 일본 전국은 파일로 수십 GB.

`bldg_tile` 은 "이 타일은 적재 완료" 표시 = 권위 원천. --mark-bbox 를 주면 건물이 하나도 없는 타일도
적재 완료로 표시한다(바다·공원 등 진짜 개활). 표시가 없으면 실시간 조회로 폴백하는데, 해외는
V-World 가 없고 서버에서 Overpass 가 막혀 있어 결국 건물 0 이 되므로 반드시 표시해야 한다.

  osmium extract -b W,S,E,N ~/data/japan-latest.osm.pbf -o /tmp/m.osm.pbf
  osmium tags-filter /tmp/m.osm.pbf w/building a/building -o /tmp/mb.osm.pbf
  osmium export -f geojsonseq --add-unique-id=type_id /tmp/mb.osm.pbf -o /tmp/mb.geojsonl
  ... api python3 /repo/scripts/load_osm_bldg_to_db.py /data/mb.geojsonl --mark-bbox S W N E --src osm-jp
"""
from __future__ import annotations
import argparse, asyncio, json, math, sys, time
sys.path.insert(0, "/app")

KEEP = ("building", "building:material", "building:levels", "building:levels:underground",
        "height", "min_height", "name", "amenity", "shop", "office")
INSERT = ("INSERT INTO bldg_poly (id, tags, geom, tkey) VALUES ($1,$2::jsonb,ST_GeomFromText($3,4326),$4) "
          "ON CONFLICT (id) DO UPDATE SET tags=EXCLUDED.tags, geom=EXCLUDED.geom, tkey=EXCLUDED.tkey")
MARK = ("INSERT INTO bldg_tile (tkey, n, src, built_at) VALUES ($1,$2,$3,NOW()) "
        "ON CONFLICT (tkey) DO UPDATE SET n=EXCLUDED.n, src=EXCLUDED.src, built_at=NOW()")


def tkey(lat: float, lon: float) -> str:
    return f"{int(math.floor(lat * 100))}_{int(math.floor(lon * 100))}"


def wkt(ring: list) -> str | None:
    if len(ring) < 4:
        return None
    pts = ",".join(f"{x:.6f} {y:.6f}" for x, y in ring)
    if ring[0] != ring[-1]:
        pts += f",{ring[0][0]:.6f} {ring[0][1]:.6f}"
    return f"POLYGON(({pts}))"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojsonl"); ap.add_argument("--src", default="osm")
    ap.add_argument("--mark-bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"))
    ap.add_argument("--batch", type=int, default=5000)
    a = ap.parse_args()
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 접속 실패"); return
    async with pool.acquire() as c:
        await c.execute("CREATE TABLE IF NOT EXISTS bldg_poly (id TEXT PRIMARY KEY, tags JSONB NOT NULL, "
                        "geom geometry(Polygon,4326) NOT NULL, tkey TEXT NOT NULL)")
        await c.execute("CREATE INDEX IF NOT EXISTS ix_bldg_poly_geom ON bldg_poly USING GIST (geom)")
        await c.execute("CREATE INDEX IF NOT EXISTS ix_bldg_poly_tkey ON bldg_poly (tkey)")
        await c.execute("CREATE TABLE IF NOT EXISTS bldg_tile (tkey TEXT PRIMARY KEY, n INTEGER NOT NULL, "
                        "src TEXT, built_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
    t0 = time.time(); n = skip = 0; buf = []; per_tile: dict[str, int] = {}

    async def flush():
        nonlocal buf
        if buf:
            async with pool.acquire() as c:
                await c.executemany(INSERT, buf)
            buf = []

    with open(a.geojsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line.startswith("{"):
                continue
            ft = json.loads(line); p = ft.get("properties") or {}
            if "building" not in p:
                skip += 1; continue
            oid = str(ft.get("id") or p.get("@id") or "")
            if not oid or oid[0] not in "wra" or not oid[1:].isdigit():
                skip += 1; continue
            g = ft.get("geometry") or {}; t = g.get("type"); c_ = g.get("coordinates") or []
            rings = [c_[0]] if t == "Polygon" else ([pp[0] for pp in c_ if pp] if t == "MultiPolygon" else [])
            if not rings:
                skip += 1; continue
            tags = {k: p[k] for k in KEEP if k in p}
            for k, ring in enumerate(rings):
                w = wkt(ring)
                if w is None:
                    continue
                tk = tkey(ring[0][1], ring[0][0])
                buf.append((f"{a.src}/{oid}" + (f":{k}" if k else ""), json.dumps(tags, ensure_ascii=False), w, tk))
                per_tile[tk] = per_tile.get(tk, 0) + 1
                n += 1
            if len(buf) >= a.batch:
                await flush()
                if n % 200000 < a.batch:
                    print(f"  건물 {n:,} ({time.time()-t0:.0f}s)", flush=True)
    await flush()
    marks = dict(per_tile)
    if a.mark_bbox:
        S, W, N, E = a.mark_bbox
        for la in range(int(math.floor(S * 100)), int(math.floor(N * 100)) + 1):
            for lo in range(int(math.floor(W * 100)), int(math.floor(E * 100)) + 1):
                marks.setdefault(f"{la}_{lo}", 0)
    async with pool.acquire() as c:
        await c.executemany(MARK, [(k, v, a.src) for k, v in marks.items()])
        tot = await c.fetchval("SELECT COUNT(*) FROM bldg_poly")
        await c.execute("ANALYZE bldg_poly")
    print(f"적재 {n:,}동 (건너뜀 {skip:,}) / 타일 {len(marks):,}개 표시 → bldg_poly 총 {tot:,}행  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
