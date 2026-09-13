#!/usr/bin/env python3
"""실측 80지점 × 가로수 적재 검증 (2026-09-12).

왜: 가로수를 구간+그루수에서 점으로 전개했으므로, 개별 나무의 위치는 추정이다.
    "엉뚱한 데 찍혔는지"를 판단할 유일한 기준이 실측 지점과의 거리다.
    대표님 증언 — "실측한 지점들은 거의 나무가 없었어" — 가 대조군이다.
    반경 30m(엔진 TREE_SEARCH_M) 안에 나무가 잡히는 지점이 지나치게 많으면 오적재다.

  docker exec climax-api python3 /tmp/check_trees_80.py /tmp/tier3_geometry_80_260912.csv
"""
from __future__ import annotations
import asyncio, csv, sys
sys.path.insert(0, "/app")

Q = """
SELECT src, COUNT(*) n,
       MIN(ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint($1,$2),4326)::geography)) d
  FROM tree_point
 WHERE ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint($1,$2),4326)::geography, $3)
 GROUP BY src
"""


async def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/tier3_geometry_80_260912.csv"
    radius = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))

    def col(r, *names):
        for k in names:
            for c, v in r.items():
                if c and c.strip().lower() == k:
                    return v
        return None

    from app.services.skyline import _get_pool
    pool = await _get_pool()
    hit = 0
    per_src = {}
    detail = []
    async with pool.acquire() as c:
        for r in rows:
            la = col(r, "lat", "위도"); lo = col(r, "lon", "lng", "경도")
            if la is None or lo is None:
                continue
            la, lo = float(la), float(lo)
            recs = await c.fetch(Q, lo, la, radius)
            if recs:
                hit += 1
                near = min(float(x["d"]) for x in recs)
                tot = sum(int(x["n"]) for x in recs)
                detail.append((col(r, "name", "지점", "site") or f"{la:.5f},{lo:.5f}", tot, near))
                for x in recs:
                    per_src[x["src"]] = per_src.get(x["src"], 0) + 1
    n = len(rows)
    print(f"실측 {n}지점 / 반경 {radius:.0f}m")
    print(f"가로수 있음 {hit}지점 ({hit/max(n,1)*100:.0f}%)  없음 {n-hit}지점")
    print("출처별 지점수:", ", ".join(f"{k} {v}" for k, v in sorted(per_src.items(), key=lambda t: -t[1])) or "없음")
    if detail:
        detail.sort(key=lambda t: t[2])
        print("가장 가까운 10지점 (지점명, 30m내 그루수, 최근접 m):")
        for nm, tot, d in detail[:10]:
            print(f"   {nm:<28} {tot:>5}그루  {d:5.1f}m")
    print()
    print("판정 기준: 10~20지점이면 정상(실측지 대부분 무수목). "
          "40지점 초과면 오적재 — OFFSET_M·구간방향을 재검토할 것.")


if __name__ == "__main__":
    asyncio.run(main())
