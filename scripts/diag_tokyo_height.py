#!/usr/bin/env python3
"""도쿄 SVF 가 왜 이렇게 열려 있나 — 건물 높이 태그 보유율 (2026-09-17).

발견: 도쿄 23구 격자 224만점의 SVF 평균이 **0.749**, 43.5% 가 0.8 이상이다.
도쿄는 세계에서 가장 조밀한 도시 축에 드는데 이 값은 너무 열려 있다.

가설: 엔진의 높이 규칙은
    height 태그 있으면 그 값 (단, height > 층수×8 이면 거부)
    없으면 building:levels × 3.018 + 0.902
    둘 다 없으면 **기본 2층 = 6.938 m**
도쿄 OSM 건물 대부분에 두 태그가 다 없으면 30층 빌딩이 6.94 m 로 깔린다.
그러면 SVF 가 통째로 과대평가된다.

이 스크립트는 가정하지 않고 **태그 보유율을 직접 센다.**
비교군으로 부산도 같이 낸다 — 한국은 V-World 가 층수를 주므로 대조가 된다.

읽는 법:
  · 도쿄에서 '둘 다 없음' 비율이 높으면 -> 가설이 맞다. PLATEAU 기각 결정을 다시 봐야 한다.
  · 낮으면 -> 높이는 문제가 아니다. 격자-실시간 불일치 쪽을 먼저 판다.

  docker cp scripts/diag_tokyo_height.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/diag_tokyo_height.py 2>/dev/null
"""
from __future__ import annotations
import asyncio, sys
sys.path.insert(0, "/app")

AREAS = [
    ("도쿄 23구", 35.50, 139.55, 35.90, 139.92),
    ("  └ 신주쿠", 35.68, 139.68, 35.71, 139.72),
    ("  └ 긴자",   35.66, 139.75, 35.68, 139.78),
    ("부산 시가지", 35.05, 128.95, 35.30, 129.25),
]


async def main() -> None:
    import asyncpg
    from app.config import get_settings
    from app.services.geo import _height_m_from_props

    c = await asyncpg.connect(
        get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://"))

    print(f"\n{'구역':<14}{'건물수':>10}{'height':>9}{'levels':>9}{'둘다없음':>10}{'추정높이 중앙':>14}")
    for nm, s, w, n, e in AREAS:
        env = "geom && ST_MakeEnvelope($1,$2,$3,$4,4326)"
        tot = await c.fetchval(f"SELECT count(*) FROM bldg_poly WHERE {env}", w, s, e, n)
        if not tot:
            print(f"{nm:<14}{0:>10}   (건물 없음)")
            continue
        h = await c.fetchval(
            f"SELECT count(*) FROM bldg_poly WHERE {env} AND tags ? 'height'", w, s, e, n)
        lv = await c.fetchval(
            f"SELECT count(*) FROM bldg_poly WHERE {env} AND "
            f"(tags ? 'building:levels' OR tags ? 'gro_flo_co')", w, s, e, n)
        nei = await c.fetchval(
            f"SELECT count(*) FROM bldg_poly WHERE {env} AND NOT (tags ? 'height') AND "
            f"NOT (tags ? 'building:levels') AND NOT (tags ? 'gro_flo_co')", w, s, e, n)

        # 엔진이 실제로 쓰는 높이 — 표본 400동으로 중앙값
        rows = await c.fetch(
            f"SELECT tags FROM bldg_poly WHERE {env} ORDER BY random() LIMIT 400", w, s, e, n)
        hs = []
        for r in rows:
            import json
            t = r["tags"] if isinstance(r["tags"], dict) else json.loads(r["tags"])
            v = _height_m_from_props(t)
            if v:
                hs.append(v)
        med = sorted(hs)[len(hs) // 2] if hs else 0.0
        print(f"{nm:<14}{tot:>10,}{100*h/tot:>8.1f}%{100*lv/tot:>8.1f}%{100*nei/tot:>9.1f}%"
              f"{med:>13.2f}m")

    print("\n[엔진이 실제로 쓰는 높이 분포 — 도쿄 23구 표본 2000동]")
    rows = await c.fetch(
        "SELECT tags FROM bldg_poly WHERE geom && ST_MakeEnvelope(139.55,35.50,139.92,35.90,4326) "
        "ORDER BY random() LIMIT 2000")
    import json
    hs = []
    for r in rows:
        t = r["tags"] if isinstance(r["tags"], dict) else json.loads(r["tags"])
        v = _height_m_from_props(t)
        if v:
            hs.append(v)
    if hs:
        bands = [(0, 7), (7, 10), (10, 15), (15, 25), (25, 50), (50, 1000)]
        for lo, hi in bands:
            k = sum(1 for x in hs if lo <= x < hi)
            bar = "█" * int(50 * k / len(hs))
            print(f"  {lo:>3}~{hi:<4}m  {k:>5} ({100*k/len(hs):4.1f}%) {bar}")
        d = sum(1 for x in hs if abs(x - 6.938) < 0.01)
        print(f"\n  ⚠️ 기본값 6.94 m (태그 없어서 2층으로 가정) : {d}/{len(hs)} = {100*d/len(hs):.1f}%")

    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
