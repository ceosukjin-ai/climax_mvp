#!/usr/bin/env python3
"""V-World 건물 폴리곤을 0.01° 타일 단위로 벌크 적재 → backend/data/buildings/{tkey}.json (2026-09-11).

왜: 격자 배치가 칸마다 V-World(반경 100m)를 불러 부산 60만 칸 = 하루+. 타일(1.1km)당 한 번만 받아 두면
geo._rings_from_local 이 읽고(로컬 타일 = 권위 원천, V-World 호출 0), 격자 계산은 CPU만 → 초당 수백 칸.
층수는 저장 시 채우지 않는다 — 읽을 때 _fill_floors_from_register_many(표제부 DB)가 채운다.

  ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/fetch_vworld_tiles.py --bbox 35.05 128.95 35.30 129.25 [--threads 4] [--force] [--all-tiles]
  (저장은 /repo/backend/data/buildings — API 의 /app/data/buildings 는 :ro 마운트라 직접 못 씀)
기본은 osm_way(보행도로)가 있는 타일만(바다·산 제외). 실패 타일은 파일을 만들지 않는다(→ 실시간 폴백 유지).
"""
from __future__ import annotations
import argparse, asyncio, json, math, os, sys, time
sys.path.insert(0, "/app")
import httpx  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services.geo import VWORLD_DATA_URL, VWORLD_BUILDING_LAYER, _LOCAL_BUILDING_DIR  # noqa: E402

# --to-db: 타일을 JSON 파일이 아니라 PostGIS `bldg_poly` 에 직접 넣는다 (2026-09-11).
# 전국 타일은 파일로 약 40GB 라 WAS 디스크(50GB)에 안 들어간다. DB 는 같은 건물이 5~8GB.
INSERT_SQL = ("INSERT INTO bldg_poly (id, tags, geom, tkey) VALUES ($1,$2::jsonb,ST_GeomFromText($3,4326),$4) "
              "ON CONFLICT (id) DO UPDATE SET tags=EXCLUDED.tags, geom=EXCLUDED.geom, tkey=EXCLUDED.tkey")
TILE_SQL = ("INSERT INTO bldg_tile (tkey, n, src, built_at) VALUES ($1,$2,$3,NOW()) "
            "ON CONFLICT (tkey) DO UPDATE SET n=EXCLUDED.n, src=EXCLUDED.src, built_at=NOW()")


def _wkt(geom: list) -> str | None:
    if len(geom) < 4:
        return None
    pts = ",".join(f"{g['lon']:.6f} {g['lat']:.6f}" for g in geom)
    if geom[0] != geom[-1]:
        pts += f",{geom[0]['lon']:.6f} {geom[0]['lat']:.6f}"
    return f"POLYGON(({pts}))"


async def store_db(pool, tkey: str, els: list) -> None:
    rows = []
    for el in els:
        w = _wkt(el.get("geometry") or [])
        if w is not None:
            rows.append((f"{tkey}/{el.get('id')}", json.dumps(el.get("tags") or {}, ensure_ascii=False), w, tkey))
    async with pool.acquire() as c:
        for k in range(0, len(rows), 2000):
            await c.executemany(INSERT_SQL, rows[k:k + 2000])
        await c.execute(TILE_SQL, tkey, len(rows), "vworld-tile")

KEEP = ("bd_mgt_sn", "buld_nm", "buld_nm_dc", "gro_flo_co", "und_flo_co", "buld_no", "bul_eng_nm")
STEP = 0.01


def tkey(la: int, lo: int) -> str:
    return f"{la}_{lo}"


async def has_roads(pool, s, w, n, e) -> bool:
    if pool is None:
        return True
    async with pool.acquire() as c:
        return bool(await c.fetchval(
            "SELECT EXISTS(SELECT 1 FROM osm_way WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326) AND tags ? 'highway')",
            w, s, e, n))


async def fetch_tile(client, key, s, w, n, e) -> list[dict]:
    feats = []
    for page in range(1, 21):
        r = await client.get(VWORLD_DATA_URL, params={
            "service": "data", "request": "GetFeature", "version": "2.0", "data": VWORLD_BUILDING_LAYER, "key": key,
            "geomFilter": f"BOX({w:.6f},{s:.6f},{e:.6f},{n:.6f})", "format": "json", "size": "1000", "page": str(page),
            "geometry": "true", "attribute": "true", "crs": "EPSG:4326"})
        r.raise_for_status()
        js = r.json().get("response", {})
        if js.get("status") == "NOT_FOUND":
            break
        chunk = js.get("result", {}).get("featureCollection", {}).get("features") or []
        feats.extend(chunk)
        if len(chunk) < 1000:
            break
    out = []
    for i, f in enumerate(feats):
        g = f.get("geometry") or {}; props = f.get("properties") or {}; coords = g.get("coordinates") or []
        rings = [coords[0]] if g.get("type") == "Polygon" else [c[0] for c in coords if c]
        tags = {k: props[k] for k in KEEP if props.get(k) not in (None, "")}
        base = f"vw:{props.get('bd_mgt_sn') or i}"
        for k, ring in enumerate(rings):
            if not isinstance(ring, list) or len(ring) < 4:
                continue
            out.append({"id": base + (f":{k}" if k else ""),
                        "geometry": [{"lat": round(p[1], 6), "lon": round(p[0], 6)} for p in ring], "tags": tags})
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("S", "W", "N", "E"))
    ap.add_argument("--threads", type=int, default=4); ap.add_argument("--force", action="store_true")
    ap.add_argument("--all-tiles", action="store_true", help="도로 유무 무시하고 전 타일")
    ap.add_argument("--out", default="/repo/backend/data/buildings", help="타일 저장 폴더 (컨테이너의 /app/data/buildings 는 읽기전용 마운트)")
    ap.add_argument("--to-db", action="store_true", help="파일 대신 PostGIS bldg_poly 에 직접 적재 (전국 권장)")
    a = ap.parse_args()
    global _LOCAL_BUILDING_DIR
    _LOCAL_BUILDING_DIR = a.out
    key = get_settings().vworld_api_key
    if not key:
        print("VWORLD_API_KEY 없음"); return
    pool = None
    if not a.all_tiles or a.to_db:
        from app.services.skyline import _get_pool
        pool = await _get_pool()
        if a.to_db and pool is None:
            print("DB 접속 실패 — --to-db 불가"); return
    os.makedirs(_LOCAL_BUILDING_DIR, exist_ok=True)
    S, W, N, E = a.bbox
    tiles = [(la, lo) for la in range(int(math.floor(S * 100)), int(math.floor(N * 100)) + 1)
             for lo in range(int(math.floor(W * 100)), int(math.floor(E * 100)) + 1)]
    print(f"타일 후보 {len(tiles)}개 ({'전부' if a.all_tiles else '도로 있는 곳만'}), 스레드 {a.threads}", flush=True)
    st = {"done": 0, "ok": 0, "skip": 0, "noroad": 0, "fail": 0, "bld": 0}
    q: asyncio.Queue = asyncio.Queue()
    for t in tiles:
        q.put_nowait(t)
    t0 = time.time()

    async def worker():
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=40.0, write=10.0, pool=5.0)) as client:
            while not q.empty():
                la, lo = await q.get()
                path = os.path.join(_LOCAL_BUILDING_DIR, tkey(la, lo) + ".json")
                s, w = la / 100.0, lo / 100.0; n, e = s + STEP, w + STEP
                try:
                    if a.to_db and not a.force:
                        async with pool.acquire() as c:
                            done = await c.fetchval("SELECT 1 FROM bldg_tile WHERE tkey=$1", tkey(la, lo))
                        if done:
                            st["skip"] += 1; continue
                    if (not a.to_db) and os.path.isfile(path) and not a.force:
                        st["skip"] += 1
                    elif not await has_roads(pool, s, w, n, e):
                        st["noroad"] += 1
                    else:
                        els = None
                        for attempt in range(3):
                            try:
                                els = await fetch_tile(client, key, s, w, n, e); break
                            except Exception as ex:  # noqa: BLE001
                                if attempt == 2:
                                    raise
                                await asyncio.sleep(2 * (attempt + 1))
                        if a.to_db:
                            await store_db(pool, tkey(la, lo), els)
                        else:
                            with open(path, "w", encoding="utf-8") as f:
                                json.dump({"elements": els, "src": "vworld-tile", "fetched": int(time.time())}, f,
                                          ensure_ascii=False, separators=(",", ":"))
                        st["ok"] += 1; st["bld"] += len(els)
                except Exception as ex:  # noqa: BLE001
                    st["fail"] += 1
                    if st["fail"] <= 10:
                        print(f"  실패 {tkey(la, lo)}: {type(ex).__name__}: {ex}", flush=True)
                finally:
                    st["done"] += 1
                    if st["done"] % 100 == 0:
                        el = time.time() - t0
                        print(f"  {st['done']}/{len(tiles)} ok {st['ok']} skip {st['skip']} 도로없음 {st['noroad']} fail {st['fail']} "
                              f"건물 {st['bld']:,}  {el:.0f}s", flush=True)
                    q.task_done()

    await asyncio.gather(*(worker() for _ in range(a.threads)))
    print(f"완료: {st}  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
