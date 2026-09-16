#!/usr/bin/env python3
"""일본 건물 높이 품질 — 엔진이 무엇을 쓰고 있나 (2026-09-16).

왜:
  일본 앱의 정확도를 모른다. 실측이 0건이다(검증된 106지점은 전부 부산).
  출장 없이 잴 수 있는 것은 **입력 품질**이다. 부산에서 최대 오차원이 건물 높이였으니
  일본도 거기부터 본다.

  확인된 구조: 일본 건물은 **OSM 타일**로 들어온다. PLATEAU 는 벽 재질(構造種別)에만 쓰이고
  **높이에는 안 쓴다**. 그러면 `height` / `building:levels` OSM 태그에만 의존하고,
  둘 다 없으면 `default_floors=2` 로 떨어진다 — 2층 상수는 도쿄 도심에서 명백히 틀린다.

무엇을 찍나 (지점마다 반경 내 폴리곤 전수):
  · height 태그 보유율 / levels 보유율 / 둘 다 없는 비율
  · 계산된 높이의 분포 — 기본값(2층 ≈ 6.9 m)에 몰려 있나
  · 그래서 나오는 SVF 와, 높이를 다 채웠다면 어떻게 될지(민감도)
  · 부산 지점과 나란히 — 같은 엔진이 두 나라에서 어떻게 다른가

  docker cp scripts/diag_jp_height.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/diag_jp_height.py
"""
from __future__ import annotations
import asyncio, collections, math, statistics as st, sys
sys.path.insert(0, "/app")

PTS = [
    ("일본", "도쿄 신주쿠역",   35.6896, 139.7006),
    ("일본", "도쿄 시부야역",   35.6580, 139.7016),
    ("일본", "도쿄 마루노우치", 35.6812, 139.7671),
    ("일본", "요코하마역",     35.4657, 139.6220),
    ("일본", "오사카 우메다",   34.7025, 135.4959),
    ("일본", "나고야역",       35.1706, 136.8816),
    ("일본", "교토역",         34.9858, 135.7588),
    ("일본", "삿포로역",       43.0686, 141.3508),
    ("일본", "후쿠오카 하카타", 33.5898, 130.4207),
    ("일본", "센다이역",       38.2601, 140.8825),
    ("부산", "부산 서면",      35.1578, 129.0594),
    ("부산", "부산 서제2동",   35.2156, 129.0986),
    ("부산", "PNU point01",   35.2333, 129.0754),
]


def f(x):
    try:
        v = float(str(x).strip().split()[0])
        return v if v > 0 else None
    except (TypeError, ValueError, IndexError):
        return None


async def main():
    from app.services import geo

    print(f"\nFLOOR_PER_M={geo.FLOOR_PER_M}  ROOF_ADD_M={geo.ROOF_ADD_M}")
    print(f"기본 층수 2 → {2 * geo.FLOOR_PER_M + geo.ROOF_ADD_M:.1f} m\n")

    print(f"  {'지점':<18}{'폴리곤':>7}{'height':>8}{'levels':>8}{'둘다없음':>9}"
          f"{'기본값비율':>11}{'높이중앙':>9}{'SVF':>7}  출처")
    rows = []
    for grp, name, lat, lon in PTS:
        try:
            rings, src = await geo._rings_cached(lat, lon)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:<18} 실패: {type(e).__name__}: {e}")
            continue
        if not rings:
            print(f"  {name:<18} 건물 없음 (출처 {src})")
            continue
        n_h = n_l = 0
        hs = []
        for _g, p in rings:
            hh = f(p.get("height"))
            ll = None
            for k in ("building:levels", "gro_flo_co", "levels"):
                ll = ll or f(p.get(k))
            if hh:
                n_h += 1
            if ll:
                n_l += 1
            H = geo._height_m_from_props(p, default_floors=2)
            if H:
                hs.append(H)
        n = len(rings)
        none_both = n - len({i for i, (_g, p) in enumerate(rings)
                             if f(p.get("height")) or f(p.get("building:levels"))
                             or f(p.get("gro_flo_co"))})
        base = 2 * geo.FLOOR_PER_M + geo.ROOF_ADD_M
        n_base = sum(1 for h in hs if abs(h - base) < 0.05)
        med = st.median(hs) if hs else float("nan")
        try:
            d = await geo.svf_geometric(lat, lon)
            svf = d.get("svf")
        except Exception:  # noqa: BLE001
            svf = None
        print(f"  {name:<18}{n:>7}{100*n_h/n:>7.0f}%{100*n_l/n:>7.0f}%{100*none_both/n:>8.0f}%"
              f"{100*n_base/max(len(hs),1):>10.0f}%{med:>9.1f}"
              f"{(svf if svf is not None else float('nan')):>7.3f}  {str(src)[:10]}")
        rows.append((grp, name, n, n_h/n, n_l/n, none_both/n, n_base/max(len(hs),1), med, svf))

    print("\n나라별 요약")
    for grp in ("일본", "부산"):
        v = [r for r in rows if r[0] == grp]
        if not v:
            continue
        print(f"  {grp}  n={len(v)}   height보유 {100*st.mean(x[3] for x in v):.0f}%"
              f"   levels보유 {100*st.mean(x[4] for x in v):.0f}%"
              f"   둘다없음 {100*st.mean(x[5] for x in v):.0f}%"
              f"   기본값비율 {100*st.mean(x[6] for x in v):.0f}%")

    print("\n[민감도] 기본 2층을 4층·6층으로 바꾸면 SVF 가 얼마나 움직이나")
    print("  (높이 결측이 실제로 얼마짜리 오차인지 — 자료를 구할 값어치를 정한다)")
    print(f"  {'지점':<18}{'2층':>8}{'4층':>8}{'6층':>8}{'2→6 변화':>10}")
    for grp, name, lat, lon in PTS:
        try:
            rings, _s = await geo._rings_cached(lat, lon)
        except Exception:  # noqa: BLE001
            continue
        if not rings:
            continue
        r_out, _sn = geo._snap_outside(rings)
        r_cen, _c, _a = geo._snap_to_street_center(r_out)
        cn = geo.canopy_items(lat, lon)
        vals = []
        for df in (2, 4, 6):
            v, _nb = geo._svf_from_rings(r_cen, 1.5, 2, df, canopy=cn)
            vals.append(v)
        print(f"  {name:<18}{vals[0]:>8.3f}{vals[1]:>8.3f}{vals[2]:>8.3f}{vals[2]-vals[0]:>+10.3f}")

    print("\n읽는 법:")
    print("  · '둘다없음' 이 높으면 -> 그만큼 건물이 **2층 상수**로 계산되고 있다.")
    print("    도쿄 도심에서 2층은 명백히 틀린다. 부산의 44.8 m 오염과 같은 급의 오차원이다.")
    print("  · 민감도에서 2→6층 변화가 크면 -> 높이 자료를 구할 값어치가 크다.")
    print("    작으면 -> 이미 주변 건물이 충분히 많아 결측이 묻히는 것이다.")
    print("  · 일본 높이는 PLATEAU LOD1 의 measuredHeight 로 채울 수 있다(構造種別은 이미 쓰고 있다).")


if __name__ == "__main__":
    asyncio.run(main())
