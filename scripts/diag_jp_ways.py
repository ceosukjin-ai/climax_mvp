#!/usr/bin/env python3
"""DB 도로망이 Overpass 만큼 조밀한지 확인 (2026-09-16).

간토 적재 뒤 /route/shade 의 간선이 36,146 -> 10,744 로 3.4배 줄었다. 응답은 빨라졌지만
**도로가 빠진 것이라면 경로 품질이 나빠진 것**이라 그냥 넘기면 안 된다. 세 가지를 본다.
  1) 같은 bbox 에서 DB 의 highway way 수 / 점 수 — 부산과 자릿수를 비교
  2) 유형별 분포 — footway 같은 보행로가 통째로 빠졌는지
  3) 좌표가 살아 있는지 (osmium tags-filter 가 참조 노드를 버리면 선이 2점짜리로 뭉개진다)

  docker compose ... exec -T api python - < scripts/diag_jp_ways.py
"""
import asyncio

BOXES = [("도쿄 시부야", 35.6560, 139.6960, 35.6740, 139.7070),
         ("부산 서면", 35.1500, 129.0500, 35.1680, 129.0610)]


async def main():
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 연결 없음"); return
    async with pool.acquire() as c:
        for nm, s, w, n, e in BOXES:
            rows = await c.fetch(
                "SELECT tags->>'highway' hw, count(*) n, sum(ST_NPoints(geom)) pts "
                "FROM osm_way WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326) "
                "AND tags ? 'highway' GROUP BY 1 ORDER BY 2 DESC", w, s, e, n)
            tot = sum(r["n"] for r in rows)
            pts = sum(r["pts"] or 0 for r in rows)
            print(f"\n{nm}  (약 2 x 1 km)")
            print(f"  highway way {tot:,}   좌표점 {pts:,}   way당 평균 {pts/max(tot,1):.1f}점")
            for r in rows[:8]:
                print(f"    {r['hw']:<16}{r['n']:>6}")
    print("\n읽는 법:")
    print("  · way당 평균이 2.0 에 가까우면 -> 참조 노드가 빠져 선이 뭉개진 것이다(적재 다시).")
    print("  · footway/path 가 거의 없으면 -> 보행로가 빠진 것이다. 경로 품질이 나빠진다.")
    print("  · 도쿄 way 수가 부산보다 적으면 -> 이상하다. 도쿄가 훨씬 조밀해야 한다.")


asyncio.run(main())
