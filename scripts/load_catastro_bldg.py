#!/usr/bin/env python3
"""스페인 지적(Catastro INSPIRE) 건물 → bldg_poly 적재 (2026-09-18, 마드리드 착수).

왜 지적을 바로 넣나: 도쿄는 OSM 건물에 PLATEAU 높이를 *붙였다*. 스페인은 OSM 을 거치지 않는다 —
지적 윤곽(정확도 0.1 m)과 층수(numberOfFloorsAboveGround)가 OSM 보다 정확하고 빠짐이 적다.
지적 **건물 부분(BuildingPart)** 단위로 넣는다. 한 건물이 앞 6층·뒤 3층이면 두 폴리곤이 된다 —
SVF 에는 이게 맞다(가장 높은 부분이 하늘을 가린다).

입력: `A.ES.SDGC.BU.<시군코드>.buildingpart.gml` (zip 에서 파이프로 — 2 GB 라 풀지 않는다)
  · 좌표계 EPSG:25830 (UTM 30N). 변환은 PostGIS ST_Transform 에 맡긴다 (이미지에 pyproj 없음).
  · 층수 0 = 마당·차양·수영장 같은 부속물 → 건너뛴다. 층수 없음 → 넣되 태그 없이(엔진 기본값).
  · 외곽 링만 쓴다. 안뜰(interior ring)은 아직 무시 — 스페인 특유 문제로 따로 볼 것.

태그: building=yes, building:levels=N, source=catastro, ref:catastro=<refcat>, part=<n>
id:  catastro/<refcat>_partN[:patch]

  cd ~/spain && unzip -p A.ES.SDGC.BU.28900.zip A.ES.SDGC.BU.28900.buildingpart.gml | \
    docker run --rm -i --env-file /tmp/api.env -v $HOME/climax_mvp:/repo climax-backend:latest \
    python3 /repo/scripts/load_catastro_bldg.py - --dry --limit 5000
"""
from __future__ import annotations
import argparse, asyncio, json, math, sys, time
import xml.etree.ElementTree as ET

sys.path.insert(0, "/app")

INSERT = """
INSERT INTO bldg_poly (id, tags, geom, tkey)
SELECT $1, $2::jsonb, g,
       floor(ST_Y(p)*100)::int::text || '_' || floor(ST_X(p)*100)::int::text
FROM (SELECT ST_Transform(ST_GeomFromText($3, 25830), 4326) AS g) t,
     LATERAL (SELECT ST_PointN(ST_ExteriorRing(g), 1) AS p) q
ON CONFLICT (id) DO UPDATE SET tags=EXCLUDED.tags, geom=EXCLUDED.geom, tkey=EXCLUDED.tkey
"""
MARK = """
INSERT INTO bldg_tile (tkey, n, src, built_at)
SELECT tkey, count(*), 'catastro', NOW() FROM bldg_poly
WHERE tags->>'source' = 'catastro' GROUP BY tkey
ON CONFLICT (tkey) DO UPDATE SET n=EXCLUDED.n, src=EXCLUDED.src, built_at=NOW()
"""


def _ln(t: str) -> str:
    return t.rsplit("}", 1)[-1] if "}" in t else t


def parts(stream):
    """BuildingPart 하나씩 → (refcat, part_local_id, floors|None, [exterior posList 문자열, ...])"""
    for _ev, el in ET.iterparse(stream, events=("end",)):
        if _ln(el.tag) != "BuildingPart":
            continue
        local = None; floors = None; polys = []
        for sub in el.iter():
            n = _ln(sub.tag)
            if n == "localId" and sub.text and local is None:
                local = sub.text.strip()
            elif n == "numberOfFloorsAboveGround" and sub.text:
                try:
                    floors = int(sub.text.strip())
                except ValueError:
                    pass
            elif n == "exterior":
                for pl in sub.iter():
                    if _ln(pl.tag) == "posList" and pl.text:
                        polys.append(pl.text.split())
        el.clear()
        if local:
            refcat = local.split("_part")[0].strip()
            yield refcat, local, floors, polys


def wkt(nums: list[str]) -> str | None:
    if len(nums) < 8:            # 최소 4점(닫힘 포함)
        return None
    pts = [f"{nums[i]} {nums[i+1]}" for i in range(0, len(nums) - 1, 2)]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 4:
        return None
    return f"POLYGON(({','.join(pts)}))"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("gml", help="buildingpart.gml 경로, 또는 - (stdin)")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    import asyncpg
    from app.config import get_settings
    conn = await asyncpg.connect(
        get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://"))

    stream = sys.stdin.buffer if a.gml == "-" else open(a.gml, "rb")
    t0 = time.time()
    n_part = n_skip0 = n_nofl = n_ins = 0
    fl_hist: dict[int, int] = {}
    batch = []

    async def flush():
        nonlocal batch, n_ins
        if batch and not a.dry:
            await conn.executemany(INSERT, batch)
        n_ins += len(batch); batch = []

    for refcat, local, floors, polys in parts(stream):
        n_part += 1
        if floors == 0:
            n_skip0 += 1
            continue
        if floors is None:
            n_nofl += 1
        else:
            fl_hist[floors] = fl_hist.get(floors, 0) + 1
        tags = {"building": "yes", "source": "catastro", "ref:catastro": refcat,
                "part": local.split("_part")[-1] if "_part" in local else "1"}
        if floors:
            tags["building:levels"] = str(floors)
        for k, nums in enumerate(polys):
            w = wkt(nums)
            if not w:
                continue
            pid = f"catastro/{local.strip()}" + (f":{k}" if k else "")
            batch.append((pid, json.dumps(tags, ensure_ascii=False), w))
        if len(batch) >= 2000:
            await flush()
            if n_part % 20000 < 2000:
                print(f"  부분 {n_part:,}  적재 {n_ins:,}  층0 건너뜀 {n_skip0:,}  {time.time()-t0:.0f}s", flush=True)
        if a.limit and n_part >= a.limit:
            break
    await flush()

    print(f"\n합계: 부분 {n_part:,}  적재 {n_ins:,}  층0(부속물) 건너뜀 {n_skip0:,}  층수없음 {n_nofl:,}  {time.time()-t0:.0f}s")
    tot = sum(fl_hist.values()) or 1
    print("층수 분포:", "  ".join(f"{k}층 {100*v/tot:.0f}%" for k, v in sorted(fl_hist.items())[:12]))
    if a.dry:
        print("--dry: DB 를 바꾸지 않았다.")
    else:
        await conn.execute(MARK)
        n_t = await conn.fetchval("SELECT count(*) FROM bldg_tile WHERE src='catastro'")
        print(f"✅ bldg_tile 표시 {n_t}개 (src=catastro)  → 다음: docker restart climax-api")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
