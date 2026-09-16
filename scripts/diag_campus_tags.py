#!/usr/bin/env python3
"""캠퍼스 폴리곤에 무슨 태그가 들어 있나 (2026-09-16).

왜: 대장(층수)을 폴리곤에 붙이려는데 name 매칭이 0동이었다. 면적으로 맞추려니
    후보가 1,723개라 상대오차 3% 안에 2등이 거의 항상 있어 112동 중 110동이 애매하다.
    **면적 추측 말고 열쇠가 있는지 먼저 본다.**
    이 폴리곤은 V-World 에서 왔고(gro_flo_co 같은 건축물대장 필드명을 쓴다),
    건물명·관리번호가 들어 있으면 매칭이 추측이 아니라 조인이 된다.

  그리고 levels=1 이 **어디서 왔는지**도 본다. gro_flo_co(지상층수)가 제대로 있는데
  building:levels 만 1 이면, 적재 코드가 덮어쓴 것이다 — 그러면 대장 없이도 고쳐진다.

  docker compose ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/diag_campus_tags.py
"""
from __future__ import annotations
import asyncio, json, sys
from collections import Counter
sys.path.insert(0, "/app")

S, W, N, E = 35.2295, 129.0765, 35.2390, 129.0875


async def main():
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    if pool is None:
        print("DB 연결 없음"); return
    rows = await pool.fetch(
        "SELECT id, tags, ST_Area(geom::geography) a FROM bldg_poly "
        "WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326) AND ST_Area(geom::geography) > 300 "
        "ORDER BY ST_Area(geom::geography) DESC", W, S, E, N)
    print(f"\n캠퍼스 폴리곤 {len(rows)}개 (300 m² 이상)")

    keys = Counter()
    for r in rows:
        t = r["tags"] if isinstance(r["tags"], dict) else json.loads(r["tags"] or "{}")
        keys.update(t.keys())
    print("\n[1] 태그 키 (몇 동에 있나)")
    for k, v in keys.most_common(25):
        print(f"  {k:<28}{v:>6}")

    print("\n[2] 큰 건물 8동의 태그 전체 — 이름·번호가 있나")
    for r in rows[:8]:
        t = r["tags"] if isinstance(r["tags"], dict) else json.loads(r["tags"] or "{}")
        print(f"\n  면적 {r['a']:.0f} m²  id={r['id']}")
        for k, v in sorted(t.items()):
            print(f"    {k:<26}{str(v)[:46]}")

    print("\n[3] gro_flo_co(지상층수) vs building:levels — levels=1 이 덮어쓴 건가")
    n = await pool.fetchrow(
        """
        SELECT count(*) tot,
               count(*) FILTER (WHERE tags ? 'gro_flo_co') g,
               count(*) FILTER (WHERE (tags->>'building:levels')='1') lv1,
               count(*) FILTER (WHERE (tags->>'building:levels')='1'
                                AND NULLIF(regexp_replace(tags->>'gro_flo_co','[^0-9]','','g'),'')::int > 1) conflict
        FROM bldg_poly WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)
        """, W, S, E, N)
    print(f"  전체 {n['tot']:,}   gro_flo_co 보유 {n['g']:,}   levels=1 {n['lv1']:,}")
    print(f"  ⚠ gro_flo_co 가 2층 이상인데 levels=1 인 것: {n['conflict']:,}동")
    print("     이 숫자가 크면 대장이 없어도 gro_flo_co 로 고칠 수 있다.")


asyncio.run(main())
