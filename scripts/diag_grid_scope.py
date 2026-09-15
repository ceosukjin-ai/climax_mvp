#!/usr/bin/env python3
"""스카이라인 격자에 무엇이 들어 있나 — 비우기 전에 확인한다 (2026-09-15).

왜:
  격자를 재계산하려고 칸 수를 세어 보니 **710만 칸**이었다. 부산 22 m 격자 예상치(60만)의 10배다.
  일본이나 다른 지역이 섞여 있을 수 있다. `TRUNCATE` 하면 그게 통째로 날아간다.
  지우는 건 되돌릴 수 없으므로 **무엇이 어디에 얼마나 있는지** 먼저 본다.

  docker cp scripts/diag_grid_scope.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/diag_grid_scope.py
"""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/app")

BUSAN = (35.00, 35.40, 128.90, 129.30)      # S, N, W, E


async def main():
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 풀 없음"); return

    async with pool.acquire() as c:
        tot = await c.fetchval("SELECT count(*) FROM skyline_grid")
        print(f"\n전체 {tot:,} 칸")

        print("\n=== 출처(src)별 ===")
        rows = await c.fetch(
            "SELECT src, count(*) n, min(built_at)::date a, max(built_at)::date b "
            "FROM skyline_grid GROUP BY src ORDER BY n DESC LIMIT 15")
        for r in rows:
            print(f"  {str(r['src'])[:34]:<36}{r['n']:>10,}   {r['a']} ~ {r['b']}")

        print("\n=== 전체 좌표 범위 ===")
        r = await c.fetchrow("SELECT min(lat) a, max(lat) b, min(lon) c, max(lon) d "
                             "FROM skyline_grid")
        print(f"  위도 {r['a']:.3f} ~ {r['b']:.3f}    경도 {r['c']:.3f} ~ {r['d']:.3f}")

        s, n_, w, e = BUSAN
        inb = await c.fetchval(
            "SELECT count(*) FROM skyline_grid WHERE lat BETWEEN $1 AND $2 "
            "AND lon BETWEEN $3 AND $4", s, n_, w, e)
        print(f"\n=== 부산 bbox({s}~{n_}, {w}~{e}) ===")
        print(f"  안 {inb:,} 칸   밖 {tot - inb:,} 칸")

        print("\n=== 1도 칸별 분포 (밖에 뭐가 있나) ===")
        rows = await c.fetch(
            "SELECT floor(lat)::int la, floor(lon)::int lo, count(*) n "
            "FROM skyline_grid GROUP BY 1,2 ORDER BY n DESC LIMIT 12")
        for r in rows:
            tag = ""
            if 128 <= r["lo"] <= 129 and 34 <= r["la"] <= 35:
                tag = "  (부산·경남)"
            elif 126 <= r["lo"] <= 127 and 37 <= r["la"] <= 38:
                tag = "  (수도권)"
            elif 138 <= r["lo"] <= 140:
                tag = "  (일본 관동)"
            print(f"  N{r['la']} E{r['lo']}   {r['n']:>10,}{tag}")

        print("\n=== 부산 안에서 도로 주변만 걸러져 있나 ===")
        smp = await c.fetch(
            "SELECT lat, lon FROM skyline_grid WHERE lat BETWEEN $1 AND $2 "
            "AND lon BETWEEN $3 AND $4 LIMIT 5", s, n_, w, e)
        for x in smp:
            print(f"    {x['lat']:.4f}, {x['lon']:.4f}")
        # 격자 간격 추정
        rows = await c.fetch(
            "SELECT DISTINCT lat FROM skyline_grid WHERE lat BETWEEN $1 AND $2 "
            "ORDER BY lat LIMIT 6", s, s + 0.01)
        las = [float(x["lat"]) for x in rows]
        if len(las) >= 2:
            d = [round(las[i + 1] - las[i], 5) for i in range(len(las) - 1)]
            print(f"  위도 간격 표본 {d}  (0.0002 = 22 m)")

    print("\n읽는 법:")
    print("  · 부산 밖이 많으면 -> TRUNCATE 금지. DELETE ... WHERE 로 부산만 지운다.")
    print("  · 부산 안이 60만보다 훨씬 많으면 -> 간격이 더 촘촘한 것이다(0.0001 = 11 m).")
    print("    재계산 시간이 그만큼 늘어난다. 간격을 맞춰서 돌려야 한다.")


if __name__ == "__main__":
    asyncio.run(main())
