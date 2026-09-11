#!/usr/bin/env python3
"""스카이라인 격자 배치 — 11m 격자마다 지평선 상승각 72개·SVF·폭 사전계산 → skyline_grid (2026-09-10).

대상 격자(택1·조합):
  --cells data/momssi_geo_form.csv     CSV 의 lat,lon 열(몸씨 기록 격자 3,500 — 1차)
  --bbox S W N E [--step 0.0001]        직사각형 전수(부산 시가지 등). 건물이 60m 안에 없는 격자는 개방(SVF 1)으로 저장.
옵션: --threads 6  --resume(이미 있는 cell 건너뜀)  --limit N  --src-hint 텍스트
      --near-roads 25 (보행도로 25m 안 격자만)  --tiles-only (로컬 타일 있는 곳만 — 전국 배치는 둘 다 켤 것)
건물 원천은 geo._rings_cached 그대로(V-World → 로컬타일 → OSM). 원천이 바뀌면 --force 로 재계산.

실행(서버 컨테이너):
  ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/build_skyline_grid.py --cells /repo/data/momssi_geo_form.csv --threads 6 --resume
  ... --bbox 35.05 128.95 35.30 129.25 --step 0.0002 --threads 6 --resume     (부산, 22m 간격 ≈ 60만 격자)
"""
from __future__ import annotations
import argparse, asyncio, csv, os, sys, time
sys.path.insert(0, "/app")
from app.services import skyline as SK  # noqa: E402
from app.services.geo import _rings_cached  # noqa: E402


def cells_from_csv(path: str):
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                yield float(r["lat"]), float(r["lon"])
            except (KeyError, ValueError):
                continue


def cells_from_bbox(s, w, n, e, step):
    lat = s
    while lat <= n:
        lon = w
        while lon <= e:
            yield round(lat, 4), round(lon, 4)
            lon += step
        lat += step


async def cells_near_roads(s, w, n, e, step, margin_m):
    """보행 도로(osm_way highway)에서 margin_m 안의 격자만 (2026-09-11, 전국용).
    사람이 걷는 곳만 계산한다 — 시가지 타일 기준 셀 수 60~80% 절감. 도로망 DB가 없으면 bbox 전수로 폴백."""
    pool = await SK._get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT ST_AsGeoJSON(geom) AS g FROM osm_way WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326) "
                "AND tags ? 'highway' AND GeometryType(geom)='LINESTRING'", w, s, e, n)
    except Exception as ex:  # noqa: BLE001
        print(f"  도로망 조회 실패({ex}) → bbox 전수"); return None
    import json, math
    cells = set()
    dlat = margin_m / 110_540.0
    k = max(1, int(round(dlat / step)))
    for r in rows:
        coords = json.loads(r["g"]).get("coordinates") or []
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            dlon_m = (x1 - x0) * 111_320.0 * math.cos(math.radians(y0)); dlat_m = (y1 - y0) * 110_540.0
            L = math.hypot(dlon_m, dlat_m); nseg = max(1, int(L / (margin_m * 0.8)))
            for i in range(nseg + 1):
                t = i / nseg; la = y0 + (y1 - y0) * t; lo = x0 + (x1 - x0) * t
                if not (s <= la <= n and w <= lo <= e):
                    continue
                cla = round(round(la / step) * step, 4); clo = round(round(lo / step) * step, 4)
                for a in range(-k, k + 1):
                    for b in range(-k, k + 1):
                        cells.add((round(cla + a * step, 4), round(clo + b * step, 4)))
    return sorted(cells)


def tile_has_file(lat, lon) -> bool:
    import os
    from app.services.geo import _LOCAL_BUILDING_DIR
    return os.path.isfile(os.path.join(_LOCAL_BUILDING_DIR, f"{int(lat * 100 // 1)}_{int(lon * 100 // 1)}.json"))


async def worker(q: asyncio.Queue, stats: dict, force: bool, src_hint: str):
    while True:
        item = await q.get()
        if item is None:
            q.task_done(); return
        lat, lon = item
        try:
            if not force and await SK.get_cell(lat, lon) is not None:
                stats["skip"] += 1
            else:
                rings, src = await asyncio.wait_for(_rings_cached(lat, lon), timeout=30.0)
                sk = SK.compute_skyline_from_rings(lat, lon, rings, (src_hint or src or "none"))
                ok = await SK.upsert(sk)
                stats["ok" if ok else "fail"] += 1
                if sk.n_bld == 0:
                    stats["open"] += 1
        except Exception as e:  # noqa: BLE001
            stats["fail"] += 1
            if stats["fail"] <= 5:
                print(f"  실패 {lat},{lon}: {type(e).__name__}: {e}", flush=True)
        finally:
            stats["done"] += 1
            q.task_done()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells"); ap.add_argument("--bbox", nargs=4, type=float); ap.add_argument("--step", type=float, default=0.0001)
    ap.add_argument("--threads", type=int, default=6); ap.add_argument("--resume", action="store_true"); ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--src-hint", default="")
    ap.add_argument("--near-roads", type=float, default=0.0, metavar="M", help="보행도로에서 M m 안 격자만 (전국 배치 권장 25)")
    ap.add_argument("--tiles-only", action="store_true", help="로컬 건물 타일 파일이 있는 곳만 (V-World 실시간 호출 방지)")
    a = ap.parse_args()
    if await SK._get_pool() is None:
        print("DB 접속 실패 — 중단"); return
    gen = []
    if a.cells:
        gen.extend(cells_from_csv(a.cells))
    if a.bbox:
        near = await cells_near_roads(*a.bbox, a.step, a.near_roads) if a.near_roads > 0 else None
        gen.extend(near if near is not None else cells_from_bbox(*a.bbox, a.step))
    seen = set(); cells = []
    import math
    for c in gen:
        k = SK.cell_id(*c)
        if k not in seen:
            seen.add(k)
            if a.tiles_only and not tile_has_file(*c):
                continue
            cells.append(c)
    if a.limit:
        cells = cells[:a.limit]
    print(f"격자 {len(cells)}개, 스레드 {a.threads}, resume={a.resume} force={a.force}", flush=True)
    q: asyncio.Queue = asyncio.Queue(maxsize=a.threads * 4)
    stats = {"done": 0, "ok": 0, "skip": 0, "fail": 0, "open": 0}
    workers = [asyncio.create_task(worker(q, stats, a.force, a.src_hint)) for _ in range(a.threads)]
    t0 = time.time()
    for i, c in enumerate(cells, 1):
        await q.put(c)
        if i % 200 == 0:
            el = time.time() - t0
            print(f"  {stats['done']}/{len(cells)} ok {stats['ok']} skip {stats['skip']} fail {stats['fail']} open {stats['open']}  "
                  f"{el:.0f}s ({stats['done']/max(el,1):.1f}/s)", flush=True)
    for _ in workers:
        await q.put(None)
    await q.join()
    await asyncio.gather(*workers)
    print(f"완료: {stats}  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
