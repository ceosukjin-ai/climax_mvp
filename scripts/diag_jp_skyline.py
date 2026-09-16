#!/usr/bin/env python3
"""일본 경로에 건물 그늘이 안 잡히는 이유 확인 (2026-09-16).

`/route/shade` 가 도쿄에서 `skyline_cells: 0` 을 돌려준다. 두 가설:
  (A) 밤이라 태양이 없다 → 애초에 격자를 조회하지 않는다
  (B) 격자에 일본 칸이 없다 → 조회는 했는데 0건

`dog_course.build_graph` 에서 건물 그늘(why="bldg")은 **격자에서만** 나온다.
격자가 비면 그늘은 OSM 태그(터널·가로수길·공원)만 남고, 그래서 쾌적 경로와
최단 경로의 그늘 비율이 같아진다 — 기능이 도는 것처럼 보이지만 실은 꺼져 있다.

  docker compose -f infra/ncp/docker-compose.prod.yml exec -T api python scripts/diag_jp_skyline.py
"""
import asyncio
from datetime import datetime, timezone

SPOTS = [("도쿄 시부야", 35.6595, 139.7005), ("부산 서면", 35.1580, 129.0600)]


async def main():
    from vpti_core import estimate_solar
    from app.services import skyline as sky

    now = datetime.now(timezone.utc)
    print(f"\nUTC {now:%Y-%m-%d %H:%M}  (KST/JST +9)")
    print("\n[1] 지금 태양 — 가설 A 검증")
    for nm, la, lo in SPOTS:
        s = estimate_solar(la, lo, now)
        print(f"  {nm:<12} 고도 {s.solar_elevation_deg:+6.1f}°  방위 {s.solar_azimuth_deg:6.1f}°"
              f"   {'해 있음' if s.solar_elevation_deg > 0 else '해 없음'}")

    print("\n[2] 격자 칸 수 — 가설 B 검증")
    pool = await sky._get_pool()
    if pool is None:
        print("  DB 연결 없음")
        return
    async with pool.acquire() as c:
        tot = await c.fetchval("SELECT count(*) FROM skyline_grid")
        jp = await c.fetchval("SELECT count(*) FROM skyline_grid "
                              "WHERE lat BETWEEN 35.4 AND 36.0 AND lon BETWEEN 139.3 AND 140.0")
        kr = await c.fetchval("SELECT count(*) FROM skyline_grid "
                              "WHERE lat BETWEEN 35.0 AND 35.4 AND lon BETWEEN 128.8 AND 129.3")
    print(f"  전체 {tot:,}   도쿄권 {jp:,}   부산권 {kr:,}")

    print("\n판정:")
    print("  · 도쿄 고도가 +이고 도쿄권 칸이 0 이면 -> (B). 일본 격자를 만들어야 한다.")
    print("    그 전까지 일본의 '日陰ルート' 는 OSM 태그 그늘만 본다(건물 그늘 없음).")


if __name__ == "__main__":
    asyncio.run(main())
