#!/usr/bin/env python3
"""가로수 그늘이 실제로 작동하는가 — `tree_point` 를 엔진 풀로 확인 (2026-09-15).

왜:
  `geo._trees_near()` 는 조회에 실패하면 **조용히 빈 배열**을 돌려준다.

      except Exception as e:
          logger.debug("[tree] 조회 생략: {}", e)
          return []

  그러면 `tree_shade_factor()` 가 항상 0 이다. 즉 **테이블이 없어도 아무 오류 없이
  "나무 그늘 없음"으로 계속 돈다.** 3월 27지점에서 이 값이 전부 0 이었던 것을
  "캠퍼스 나무는 가로수가 아니라서"로 해석했는데, 그게 아니라 테이블이 없어서일 수 있다.

  psql 로 climax-postgres 컨테이너를 봤더니 tree_point 도 bldg_poly 도 없었다.
  그런데 bldg_poly 는 오늘 분명히 조회됐다 → **API 가 쓰는 DB 는 그 컨테이너가 아니다.**
  그러므로 반드시 **엔진 자신의 풀**로 확인해야 한다.

  docker cp scripts/diag_tree_table.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/diag_tree_table.py
"""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/app")

PTS = [("PNU point01", 35.233287, 129.075365),
       ("서면 교차로", 35.1578, 129.0594),
       ("온천천 산책로", 35.2245, 129.0846),
       ("해운대 해변로", 35.1585, 129.1600)]


async def main():
    from app.services import geo
    from app.services.skyline import _get_pool

    pool = await _get_pool()
    if pool is None:
        print("DB 풀 없음 — 중단"); return

    async with pool.acquire() as c:
        who = await c.fetchrow("SELECT current_database() db, inet_server_addr() host, "
                               "inet_server_port() port")
        print(f"\n엔진이 쓰는 DB: {who['db']} @ {who['host']}:{who['port']}")

        rows = await c.fetch(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_name ILIKE '%tree%' OR table_name ILIKE '%garosu%' "
            "ORDER BY 1,2")
        print(f"\n나무 관련 테이블 {len(rows)}개")
        for r in rows:
            print(f"  {r['table_schema']}.{r['table_name']}")
        if not any(r["table_name"] == "tree_point" for r in rows):
            print("\n  !! tree_point 가 없다 -> 가로수 그늘은 지금까지 한 번도 작동하지 않았다.")
        else:
            n = await c.fetchval("SELECT count(*) FROM tree_point")
            print(f"\n  tree_point {n:,}행")
            if n:
                smp = await c.fetch("SELECT ST_Y(geom) la, ST_X(geom) lo, h, r "
                                    "FROM tree_point LIMIT 5")
                for s in smp:
                    print(f"    {s['la']:.5f},{s['lo']:.5f}  높이={s['h']} 수관반경={s['r']}")

    # ── 태양고도·방위를 훑는다 ────────────────────────────────────────────────
    # 첫 시험을 태양고도 60도로 했는데 전부 0 이 나왔다. 그건 버그가 아니라 **물리**일 수 있다.
    #   수고 18 m, 눈높이 1.5 m -> 60도를 가리려면 나무가 9.5 m 안에 있어야 한다.
    #   게다가 그 방위(수관 반각 + 2도) 안에 있어야 한다.
    # 그래서 고도를 낮추고 방위를 한 바퀴 돌려 **최대 그늘율**을 본다. 이게 옳은 시험이다.
    print("\n실제 호출 — 태양 방위 36방향 x 고도 여러 개, 각 지점의 최대 그늘율")
    print(f"  {'지점':<16}{'30m내 나무':>10}{'최근접m':>8}"
          f"{'고도15':>8}{'고도30':>8}{'고도45':>8}{'고도60':>8}")
    import math as _m
    for name, lat, lon in PTS:
        try:
            trees = await geo._trees_near(lat, lon)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:<16}  조회 예외: {type(e).__name__}: {e}")
            continue
        near = []
        for tla, tlo, h, r in trees:
            x, y = geo._to_local_m(tla, tlo, lat, lon)
            near.append((_m.hypot(x, y), _m.degrees(_m.atan2(x, y)) % 360.0, h, r))
        near.sort()
        n30 = sum(1 for d, _a, _h, _r in near if d <= geo.TREE_SEARCH_M)
        dmin = near[0][0] if near else float("nan")
        cells = []
        for el in (15.0, 30.0, 45.0, 60.0):
            best = 0.0
            for az in range(0, 360, 10):
                try:
                    f = await geo.tree_shade_factor(lat, lon, float(az), el)
                except Exception as e:  # noqa: BLE001
                    print(f"  ({name} shade 예외 {type(e).__name__}: {e})")
                    f = 0.0
                best = max(best, f)
            cells.append(best)
        print(f"  {name:<16}{n30:>10}{dmin:>8.1f}" + "".join(f"{c:>8.2f}" for c in cells))
        for d, az, h, r in near[:4]:
            top = _m.degrees(_m.atan2(max(h - 1.5, 0.1), max(d, 0.01)))
            print(f"      거리 {d:5.1f} m  방위 {az:5.1f}도  수고 {h:4.1f} m  "
                  f"수관반경 {r:.1f}  -> 이 나무가 가리는 최대 태양고도 {top:.0f}도")

    print("\n읽는 법:")
    print("  · 고도 15~30도에서 값이 나오면 -> 함수는 정상이다. 60도에서 0 인 건 물리적으로 맞다")
    print("    (해가 높으면 나무가 바로 위에 있어야 가린다).")
    print("  · 모든 고도·방위에서 0 이면 -> 함수나 좌표 변환에 문제가 있다.")
    print("  · '가리는 최대 태양고도' 가 전부 낮으면 -> 나무가 너무 멀리 놓여 있다는 뜻이다.")
    print("    9/12 메모의 OFFSET_M(중심선 ±5 m) 문제가 여기로 이어진다.")


if __name__ == "__main__":
    asyncio.run(main())
