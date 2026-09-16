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
    # levels=1 비율만으로는 못 가린다 (2026-09-16). 보수동 25%·중앙값 1층은 실제로 단층집이
    # 많은 동네라 정상일 수 있다. 진짜 신호는 **height 가 levels 와 모순돼 엔진이 버리는 것**이다:
    # 제2공학관은 height 44.8 / levels 1 이라 `height > 층수 x 8` 규칙에 걸려 44.8 을 버리고
    # levels 1 -> 3.9 m 로 떨어진다. 그 결과 5층 건물이 하늘을 안 막는다.
    # '버림+1층' 이 그 지역에서 얼마나 되는지가 오염의 크기다.
    print(f"\n{'구역':<14}{'건물':>9}{'lv=1':>7}{'중앙':>5}"
          f"{'height버림':>10}{'버림+1층':>9}{'비율':>6}{'height종류':>11}{'최빈height':>11}")
    for nm, s, w, n, e in AREAS:
        r = await pool.fetchrow(
            """
            WITH b AS (
              SELECT NULLIF(substring(btrim(tags->>'height') from '^[0-9]+(?:\.[0-9]+)?'),'')::float h,
                     NULLIF(regexp_replace(tags->>'building:levels','[^0-9]','','g'),'')::int fl
              FROM bldg_poly WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)
            )
            SELECT count(*) tot,
                   count(*) FILTER (WHERE fl=1) lv1,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY fl) med,
                   count(*) FILTER (WHERE h IS NOT NULL AND fl IS NOT NULL AND h > fl*8.0) rej,
                   count(*) FILTER (WHERE h IS NOT NULL AND fl=1 AND h > 8.0) rej1
            FROM b
            """, w, s, e, n)
        hv = await pool.fetchrow(
            """
            SELECT count(DISTINCT tags->>'height') k,
                   mode() WITHIN GROUP (ORDER BY tags->>'height') m
            FROM bldg_poly WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326) AND tags ? 'height'
            """, w, s, e, n)
        src = await pool.fetchval(
            "SELECT string_agg(DISTINCT src, ',') FROM bldg_tile "
            "WHERE tkey = ANY($1::text[])",
            [f"{la}_{lo}" for la in range(int(s*100), int(n*100)+1)
             for lo in range(int(w*100), int(e*100)+1)])
        tot = r["tot"] or 0
        print(f"{nm:<14}{tot:>9,}{r['lv1'] or 0:>7,}{str(r['med']):>5}"
              f"{r['rej'] or 0:>10,}{r['rej1'] or 0:>9,}"
              f"{(100*(r['rej1'] or 0)/max(tot,1)):>5.0f}%"
              f"{hv['k'] or 0:>11,}{str(hv['m'])[:9]:>11}")
        _ = src
    print("\n읽는 법:")
    print("  · 'height버림' — height 가 층수 x 8 을 넘어 엔진이 버린 건물. 오염의 직접 증거다.")
    print("  · '버림+1층' — 버린 뒤 levels=1 로 떨어져 **3.9 m 가 된** 건물. 이게 하늘을 안 막는다.")
    print("  · 'height종류' 가 건물 수에 비해 아주 적고 '최빈height' 가 한 값에 몰려 있으면,")
    print("    지번 하나로 묶여 같은 값이 박힌 것이다(캠퍼스 44.8 처럼).")
    print("  · '버림+1층 비율' 이 캠퍼스만 높으면 국소, 부산 전역이 높으면 논문 수치를 다시 내야 한다.")


asyncio.run(main())
