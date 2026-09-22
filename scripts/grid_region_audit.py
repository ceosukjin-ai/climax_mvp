#!/usr/bin/env python3
"""권역 격자 재계산 뒤 검수 (2026-09-21) — climax-api 안에서 실행.

  docker cp scripts/grid_region_audit.py climax-api:/tmp/
  docker exec -i -e BBOX=37.40,37.72,126.76,127.20 -e SRC=vwtile+reg+canopy-260920 \
      climax-api python3 /tmp/grid_region_audit.py

세 가지를 본다 (부산·전국 점검과 같은 절차):
  [1] 라벨 분포     — 새 판(SRC) 이외 행이 남았나 (유령 행)
  [2] 무작위 표본   — 저장값 vs 지금 코드로 다시 계산한 값 (0.00x 면 정상)
  [3] 규모 지표     — 「건물 20동 이상인데 SVF > 0.95」 비율 (정상 5~6 %, 수관 없는 판 16.7 %)
DB 에는 아무것도 쓰지 않는다.
"""
import asyncio, os, random, statistics as st, sys
sys.path.insert(0, "/app")
N = int(os.environ.get("N", "12"))


async def main():
    import asyncpg
    from app.config import get_settings
    from app.services import geo, skyline as SK
    s, n, w, e = map(float, os.environ["BBOX"].split(","))
    SRC = os.environ.get("SRC", "vwtile+reg+canopy-260920")
    BB = f"lat BETWEEN {s} AND {n} AND lon BETWEEN {w} AND {e}"
    c = await asyncpg.connect(get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://"))

    print(f"\n권역 {os.environ['BBOX']}   기준 라벨 {SRC}")
    rows = await c.fetch(f"SELECT src, count(*) k FROM skyline_grid WHERE {BB} GROUP BY src ORDER BY k DESC")
    tot = sum(r["k"] for r in rows)
    print(f"[1] 라벨 분포 (총 {tot:,})")
    for r in rows:
        print(f"      {r['src'] or '(없음)':<28} {r['k']:>10,}  {100*r['k']/tot:5.2f} %")

    rs = await c.fetch(f"SELECT lat, lon, svf, n_bld FROM skyline_grid TABLESAMPLE SYSTEM (0.2) "
                       f"WHERE {BB} AND src=$1 AND n_bld > 0", SRC)
    rs = random.Random(7).sample(list(rs), min(N, len(rs)))
    print(f"\n[2] 무작위 {len(rs)}칸 — 저장값 vs 재계산")
    diffs = []
    for r in rs:
        rings, src = await geo._rings_cached(r["lat"], r["lon"])
        from app.services.geo import _trees_near, _trees_local, TREE_SVF_ON
        _tr = (_trees_local(r["lat"], r["lon"], await _trees_near(r["lat"], r["lon"]))
               if TREE_SVF_ON else [])
        sk = SK.compute_skyline_from_rings(r["lat"], r["lon"], list(rings or []), src or "check",
                                           trees=_tr)
        d = sk.svf - r["svf"]; diffs.append(abs(d))
        print(f"      {r['lat']:.4f},{r['lon']:.4f}  저장 {r['svf']:.3f}  재계산 {sk.svf:.3f}  "
              f"차이 {d:+.3f}  건물 {r['n_bld']}")
    if diffs:
        print(f"      |차이| 중앙 {st.median(diffs):.3f}  최대 {max(diffs):.3f}   "
              f"→ {'정상' if max(diffs) < 0.02 else '확인 필요'}")

    k20 = await c.fetchval(f"SELECT count(*) FROM skyline_grid WHERE {BB} AND n_bld >= 20")
    k95 = await c.fetchval(f"SELECT count(*) FROM skyline_grid WHERE {BB} AND n_bld >= 20 AND svf > 0.95")
    print(f"\n[3] 건물 20동+ 인데 SVF > 0.95 : {k95:,} / {k20:,} = {100*k95/max(k20,1):.1f} %  "
          f"(정상 5~6 %, 수관 없는 판 16.7 %)")
    await c.close()


asyncio.run(main())
