#!/usr/bin/env python3
"""가로수 그늘 층 민감도 — 정답지 없이 "이 층이 작동은 하는가"만 본다 (2026-09-13).

왜 필요한가:
  부산 실측 80지점에서는 가로수로 판정이 바뀐 곳이 (아마) 0 이다. 그런데 그 0 이
  "부산에 OSM 가로수가 없어서"인지 "코드가 데이터에 닿지 못해서"인지 구분이 안 된다.
  가로수가 빽빽한 지역에서 돌려보면 갈린다. 정답지가 필요 없다 — 뒤집히는 비율만 본다.

  0 % 면  : 층이 데이터에 닿지 못한다. 적재/코드 문제. 실측을 더 해도 소용없다.
  10~20 % : 층은 살아 있다. 남은 것은 정확도이고, 그때 라벨이 필요하다.

쓰는 법 (컨테이너 안):
  python3 /tmp/shade_sensitivity.py --bbox 126.85 35.13 126.95 35.20 --when "2026-08-20 14:00"
    --bbox  minlon minlat maxlon maxlat
    --when  KST. 안에서 UTC 로 바꾼다.
    --n     표본 수 (기본 300)

지역 예 (OSM 가로수 밀집):
  광주 상무지구   126.85 35.13 126.95 35.20
  서울 강남       127.02 37.49 127.08 37.53
  서울 여의도     126.91 37.51 126.94 37.54
  부산 (대조군)   129.05 35.14 129.12 35.20
"""
from __future__ import annotations
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    ap.add_argument("--when", required=True, help='KST, "YYYY-MM-DD HH:MM"')
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    from vpti_core.solar import estimate_solar
    from app.services.geo import sun_blocked_outdoor, tree_shade_factor

    minlon, minlat, maxlon, maxlat = a.bbox
    when = (datetime.strptime(a.when, "%Y-%m-%d %H:%M")
            - timedelta(hours=9)).replace(tzinfo=timezone.utc)

    # 정사각에 가까운 격자로 n 점을 고르게 뿌린다.
    side = max(2, int(round(a.n ** 0.5)))
    pts = []
    for i in range(side):
        for j in range(side):
            pts.append((minlat + (maxlat - minlat) * (i + 0.5) / side,
                        minlon + (maxlon - minlon) * (j + 0.5) / side))
    pts = pts[:a.n]

    n_blocked = 0          # 건물이 가림
    n_tree_any = 0         # 나무 차광률 > 0
    n_flip = 0             # 나무 때문에 볕 -> 그늘 로 뒤집힘
    tf_vals = []
    errors = 0
    el0 = None

    for lat, lon in pts:
        try:
            sol = estimate_solar(lat, lon, when)
            if el0 is None:
                el0 = sol.solar_elevation_deg
            if sol.solar_elevation_deg <= 0:
                continue
            blocked, _why = await sun_blocked_outdoor(
                lat, lon, sol.solar_azimuth_deg, sol.solar_elevation_deg)
            if blocked:
                n_blocked += 1
                continue
            tf = await tree_shade_factor(
                lat, lon, sol.solar_azimuth_deg, sol.solar_elevation_deg)
            if tf > 0:
                n_tree_any += 1
                tf_vals.append(tf)
            if (1.0 - tf) < 0.5:      # shade_hit_80.py 와 같은 임계값
                n_flip += 1
        except Exception as exc:                      # noqa: BLE001
            errors += 1
            if errors <= 3:
                print(f"  ! {type(exc).__name__}: {exc}")

    n = len(pts)
    tag = f" [{a.label}]" if a.label else ""
    print(f"\n표본 {n}점{tag}   bbox {minlon},{minlat} ~ {maxlon},{maxlat}")
    print(f"시각 {a.when} KST   태양고도 {el0:.1f}°" if el0 is not None else "")
    print(f"  건물이 가림           {n_blocked:4d}  ({100*n_blocked/n:5.1f} %)")
    print(f"  나무 차광률 > 0       {n_tree_any:4d}  ({100*n_tree_any/n:5.1f} %)")
    print(f"  나무로 그늘 판정 뒤집힘 {n_flip:4d}  ({100*n_flip/n:5.1f} %)   <-- 이 숫자를 본다")
    if tf_vals:
        tf_vals.sort()
        mid = tf_vals[len(tf_vals) // 2]
        print(f"  차광률 (>0 인 것만)   중앙값 {mid:.2f}  최대 {tf_vals[-1]:.2f}")
    if errors:
        print(f"  오류 {errors}건")
    print()
    if n_tree_any == 0:
        print("  나무 차광률이 전부 0 이다. 이 구역에 OSM 가로수가 없거나,")
        print("  가로수 조회가 데이터에 닿지 못하고 있다. 적재 상태부터 확인할 것.")
    elif n_flip == 0:
        print("  나무는 찾았으나 판정을 뒤집지는 못했다 — 수관이 작거나 태양이 높다.")
        print("  시각을 오후로 바꿔(태양고도 낮게) 다시 볼 것.")
    else:
        print("  가로수 층이 작동한다. 남은 것은 정확도이며, 이제 라벨이 필요하다.")


if __name__ == "__main__":
    asyncio.run(main())
