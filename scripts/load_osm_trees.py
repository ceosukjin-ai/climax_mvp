#!/usr/bin/env python3
"""OSM 나무(natural=tree, tree_row) → PostGIS `tree_point` (2026-09-12).

왜: 직사광을 막는 건 건물만이 아니다. 가로수·공원 나무가 실제로 사람 위를 덮는다.
위성 NDVI 는 반경 평균이라 "머리 위에 나무가 있나"를 모른다 → 개별 나무 좌표가 필요하다.
tree_row(가로수 열)는 라인이므로 8m 간격으로 점을 찍어 넣는다.

  osmium tags-filter <pbf> n/natural=tree w/natural=tree_row -o trees.osm.pbf
  osmium export -f geojsonseq --add-unique-id=type_id trees.osm.pbf -o trees.geojsonl
  ... api python3 /repo/scripts/load_osm_trees.py /data/trees.geojsonl --src osm-jp
"""
from __future__ import annotations
import argparse, asyncio, json, math, sys, time
sys.path.insert(0, "/app")

DDL = """
CREATE TABLE IF NOT EXISTS tree_point (
    id   TEXT PRIMARY KEY,
    geom geometry(Point, 4326) NOT NULL,
    h    REAL,
    r    REAL,
    src  TEXT
);
CREATE INDEX IF NOT EXISTS ix_tree_point_geom ON tree_point USING GIST (geom);
"""
SQL = ("INSERT INTO tree_point (id, geom, h, r, src) VALUES ($1, ST_SetSRID(ST_MakePoint($2,$3),4326),$4,$5,$6) "
       "ON CONFLICT (id) DO UPDATE SET geom=EXCLUDED.geom, h=EXCLUDED.h, r=EXCLUDED.r, src=EXCLUDED.src")
STEP_M = 8.0


def _f(v):
    try:
        return float(str(v).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojsonl"); ap.add_argument("--src", default="osm"); ap.add_argument("--batch", type=int, default=5000)
    a = ap.parse_args()
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 접속 실패"); return
    async with pool.acquire() as c:
        for stmt in filter(None, (x.strip() for x in DDL.split(";"))):
            await c.execute(stmt)
    t0 = time.time(); n = rows_row = 0; buf = []

    async def flush():
        nonlocal buf
        if buf:
            async with pool.acquire() as c:
                await c.executemany(SQL, buf)
            buf = []

    with open(a.geojsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e")
            if not line.startswith("{"):
                continue
            ft = json.loads(line); p = ft.get("properties") or {}
            g = ft.get("geometry") or {}; t = g.get("type"); c_ = g.get("coordinates") or []
            oid = str(ft.get("id") or p.get("@id") or "")
            if not oid:
                continue
            h = _f(p.get("height")) or _f(p.get("est_height"))
            dc = _f(p.get("diameter_crown"))
            r = (dc / 2.0) if dc else None
            if t == "Point" and p.get("natural") == "tree":
                buf.append((f"{a.src}/{oid}", c_[0], c_[1], h, r, a.src)); n += 1
            elif t == "LineString" and p.get("natural") == "tree_row":
                # 가로수 열 → 8m 간격 점
                for i in range(len(c_) - 1):
                    (x0, y0), (x1, y1) = c_[i], c_[i + 1]
                    dx = (x1 - x0) * 111320.0 * math.cos(math.radians(y0)); dy = (y1 - y0) * 110540.0
                    L = math.hypot(dx, dy)
                    for k in range(max(1, int(L / STEP_M)) + 1):
                        u = min(1.0, k * STEP_M / L) if L > 0 else 0.0
                        buf.append((f"{a.src}/{oid}:{i}:{k}", x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, h, r, a.src))
                        n += 1; rows_row += 1
            if len(buf) >= a.batch:
                await flush()
                if n % 200000 < a.batch:
                    print(f"  나무 {n:,} ({time.time()-t0:.0f}s)", flush=True)
    await flush()
    async with pool.acquire() as c:
        tot = await c.fetchval("SELECT COUNT(*) FROM tree_point")
        await c.execute("ANALYZE tree_point")
    print(f"적재 {n:,}그루(가로수열 전개 {rows_row:,}) → tree_point 총 {tot:,}행  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
