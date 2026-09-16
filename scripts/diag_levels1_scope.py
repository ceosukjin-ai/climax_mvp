#!/usr/bin/env python3
"""`building:levels=1` 이 어디까지 퍼져 있나 (2026-09-16).

발견: 부산대 27지점 주변 건물이 거의 다 3.9 m 로 들어가 있다.
      3.9 = 1층 x 3.018 + 0.902 — 기본값이 아니라 **levels=1 이 실제로 태그돼 있다.**
      그래서 캠퍼스 svf_bldg_only 가 0.985(중앙값)로, 건물이 하늘을 거의 안 막는다.

왜 범위를 재나: 캠퍼스만이면 국소 문제지만, 부산 전역이면 논문 수치가 통째로 흔들린다.
  8월 79지점의 svf_bldg_only 중앙값은 0.662 라 시내는 정상으로 보이지만, **세어봐야 안다.**

  docker compose ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/diag_levels1_scope.py
"""
from __future__ import annotations
import asyncio, json, sys
sys.path.insert(0, "/app")

AREAS = [
    ("부산대 캠퍼스", 35.2280, 129.0760, 35.2400, 129.0880),
    ("장전동 주변",   35.2200, 129.0700, 35.2450, 129.0950),
    ("서면",         35.1500, 129.0500, 35.1680, 129.0700),
    ("보수동",       35.1000, 129.0200, 35.1200, 129.0400),
    ("해운대",       35.1550, 129.1500, 35.1750, 129.1750),
    ("부산 전역",     35.0500, 128.9000, 35.3500, 129.3000),
]


async def main():
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 연결 없음"); return
    print(f"\n{'구역':<14}{'건물':>9}{'levels=1':>10}{'비율':>7}{'levels중앙':>11}{'height보유':>10}{'src':>22}")
    for nm, s, w, n, e in AREAS:
        r = await pool.fetchrow(
            """
            SELECT count(*) tot,
                   count(*) FILTER (WHERE (tags->>'building:levels')='1') lv1,
                   count(*) FILTER (WHERE tags ? 'height') hh,
                   percentile_disc(0.5) WITHIN GROUP (
                       ORDER BY NULLIF(regexp_replace(tags->>'building:levels','[^0-9]','','g'),'')::int
                   ) med
            FROM bldg_poly WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)
            """, w, s, e, n)
        src = await pool.fetchval(
            "SELECT string_agg(DISTINCT src, ',') FROM bldg_tile "
            "WHERE tkey = ANY($1::text[])",
            [f"{la}_{lo}" for la in range(int(s*100), int(n*100)+1)
             for lo in range(int(w*100), int(e*100)+1)])
        tot = r["tot"] or 0
        print(f"{nm:<14}{tot:>9,}{r['lv1'] or 0:>10,}"
              f"{(100*(r['lv1'] or 0)/max(tot,1)):>6.0f}%{str(r['med']):>11}"
              f"{r['hh'] or 0:>10,}{str(src)[:22]:>22}")
    print("\n읽는 법:")
    print("  · 'levels=1 비율' 이 캠퍼스만 높으면 국소 문제, 부산 전역이 높으면 논문이 흔들린다.")
    print("  · 'src' 는 그 타일을 무엇으로 적재했는지다. 구역마다 다르면 적재 경로가 범인이다.")


asyncio.run(main())
