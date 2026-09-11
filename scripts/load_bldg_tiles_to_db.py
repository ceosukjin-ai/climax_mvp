#!/usr/bin/env python3
"""건물 타일 JSON → PostGIS `bldg_poly` (2026-09-11).

왜: 타일 파일은 전국 약 40GB(WAS 디스크 50GB)로 안 들어가고, 프로세스마다 메모리에 올라가 9/11 서버 다운의
원인이 됐다. 같은 폴리곤을 PostGIS 기하로 넣으면 5~8GB·인덱스 조회이고, 계산은 한 줄도 바뀌지 않는다.
`bldg_tile` 은 "이 타일은 적재됨"을 기록 — 파일 존재 확인과 같은 역할(적재된 곳은 DB 가 권위 원천).

  ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/load_bldg_tiles_to_db.py \
      --dir /repo/backend/data/buildings [--only 3518_12910] [--replace]
"""
from __future__ import annotations
import argparse, asyncio, json, os, sys, time
sys.path.insert(0, "/app")

DDL = """
CREATE TABLE IF NOT EXISTS bldg_poly (
    id    TEXT PRIMARY KEY,
    tags  JSONB NOT NULL,
    geom  geometry(Polygon, 4326) NOT NULL,
    tkey  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_bldg_poly_geom ON bldg_poly USING GIST (geom);
CREATE INDEX IF NOT EXISTS ix_bldg_poly_tkey ON bldg_poly (tkey);
CREATE TABLE IF NOT EXISTS bldg_tile (
    tkey     TEXT PRIMARY KEY,
    n        INTEGER NOT NULL,
    src      TEXT,
    built_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def wkt(geom: list) -> str | None:
    if len(geom) < 4:
        return None
    pts = ",".join(f"{g['lon']:.6f} {g['lat']:.6f}" for g in geom)
    if geom[0] != geom[-1]:
        pts += f",{geom[0]['lon']:.6f} {geom[0]['lat']:.6f}"
    return f"POLYGON(({pts}))"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True); ap.add_argument("--only", default=None)
    ap.add_argument("--replace", action="store_true"); ap.add_argument("--batch", type=int, default=5000)
    a = ap.parse_args()
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 접속 실패"); return
    async with pool.acquire() as c:
        for stmt in filter(None, (x.strip() for x in DDL.split(";"))):
            await c.execute(stmt)
        if a.replace:
            await c.execute("TRUNCATE bldg_poly"); await c.execute("TRUNCATE bldg_tile")
    files = sorted(f for f in os.listdir(a.dir) if f.endswith(".json") and not f.startswith("_"))
    if a.only:
        files = [f for f in files if f[:-5] == a.only]
    t0 = time.time(); tot = bad = 0
    sql = "INSERT INTO bldg_poly (id, tags, geom, tkey) VALUES ($1,$2::jsonb,ST_GeomFromText($3,4326),$4) ON CONFLICT (id) DO UPDATE SET tags=EXCLUDED.tags, geom=EXCLUDED.geom, tkey=EXCLUDED.tkey"
    for i, fn in enumerate(files, 1):
        tkey = fn[:-5]
        try:
            els = (json.load(open(os.path.join(a.dir, fn), encoding="utf-8")) or {}).get("elements") or []
        except Exception as e:  # noqa: BLE001
            print(f"  건너뜀(깨진 타일) {tkey}: {e}"); bad += 1; continue
        rows = []
        for el in els:
            w = wkt(el.get("geometry") or [])
            if w is None:
                continue
            rows.append((f"{tkey}/{el.get('id')}", json.dumps(el.get("tags") or {}, ensure_ascii=False), w, tkey))
        async with pool.acquire() as c:
            for k in range(0, len(rows), a.batch):
                await c.executemany(sql, rows[k:k + a.batch])
            await c.execute("INSERT INTO bldg_tile (tkey, n, src, built_at) VALUES ($1,$2,$3,NOW()) "
                            "ON CONFLICT (tkey) DO UPDATE SET n=EXCLUDED.n, src=EXCLUDED.src, built_at=NOW()",
                            tkey, len(rows), "tiles")
        tot += len(rows)
        if i % 100 == 0:
            print(f"  타일 {i}/{len(files)} 건물 {tot:,} ({time.time()-t0:.0f}s)", flush=True)
    async with pool.acquire() as c:
        n1 = await c.fetchval("SELECT COUNT(*) FROM bldg_poly"); n2 = await c.fetchval("SELECT COUNT(*) FROM bldg_tile")
        await c.execute("ANALYZE bldg_poly")
    print(f"적재 {tot:,}동 / 타일 {len(files)}개(깨짐 {bad}) → bldg_poly {n1:,}행, bldg_tile {n2:,}행  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
