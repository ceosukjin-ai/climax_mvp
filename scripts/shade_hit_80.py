#!/usr/bin/env python3
"""그늘 판정 적중률 — 건물만 vs 건물+가로수 (2026-09-12).

왜 이 시험인가:
  run_tier3_geometry_80.py 는 실측자가 적은 `볕`(0/1) 을 정답으로 **넣어 주고** PET 를 맞힌다.
  그래서 가로수를 아무리 넣어도 그 스크립트의 결과는 변하지 않는다 — 그늘 여부가 이미 주어졌으니까.
  앱은 정반대다. 옆에서 알려주는 사람이 없으므로 **엔진이 그늘 여부를 스스로 맞혀야** 한다.
  가로수 층의 가치는 바로 거기서 생긴다 → `볕` 을 정답(ground truth)으로 삼아
  엔진의 그늘 예측을 채점한다. 건물만 / 건물+가로수 두 번.

routes.py 와 동일한 규약:
  blocked = sun_blocked_outdoor(...)              # 건물이 태양을 가림
  tree_f  = tree_shade_factor(...)  (blocked 아닐 때만)
  direct_shade = 0.0 if blocked else (1.0 - tree_f)
  → direct_shade 가 1.0 이면 완전 양지, 0.0 이면 완전 그늘.
  실측 `볕`=1 이 양지이므로, 예측 양지 판정은 direct_shade >= THR 로 본다.

  docker exec climax-api python3 /tmp/shade_hit_80.py /tmp/pts80.csv
"""
from __future__ import annotations
import asyncio, csv, sys
from datetime import datetime, timezone
sys.path.insert(0, "/app")

THR = 0.5      # 이 아래면 '그늘'로 예측한 것으로 본다 (나무 차광 최대 0.85 → 0.15)


def mat(tp, fp, fn, tn, label):
    n = tp + fp + fn + tn
    acc = (tp + tn) / n if n else 0.0
    # '그늘'을 양성으로 본다 — 폭염에서 놓치면 안 되는 쪽이 그늘 놓침이 아니라 '양지인데 그늘이라 함'
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    print(f"{label}")
    print(f"   정확도 {acc*100:5.1f}%   그늘 정밀도 {prec*100:5.1f}%  그늘 민감도 {rec*100:5.1f}%")
    print(f"   그늘적중 {tp:3}  양지를그늘이라함 {fp:3}  그늘을양지라함 {fn:3}  양지적중 {tn:3}")


async def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pts80.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))

    from vpti_core.solar import estimate_solar
    from app.services.geo import sun_blocked_outdoor, tree_shade_factor

    a = [0, 0, 0, 0]      # 건물만   tp fp fn tn
    b = [0, 0, 0, 0]      # +가로수
    changed = []
    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        obs_sun = str(r["볕"]).strip() == "1"          # 실측: 양지인가

        sol = estimate_solar(lat, lon, when)
        blocked, _ = await sun_blocked_outdoor(lat, lon, sol.solar_azimuth_deg,
                                               sol.solar_elevation_deg)
        tf = 0.0
        if not blocked:
            tf = await tree_shade_factor(lat, lon, sol.solar_azimuth_deg, sol.solar_elevation_deg)

        ds_a = 0.0 if blocked else 1.0                 # 건물만
        ds_b = 0.0 if blocked else (1.0 - tf)          # 건물+가로수
        for arr, ds in ((a, ds_a), (b, ds_b)):
            pred_sun = ds >= THR
            if not obs_sun and not pred_sun:   arr[0] += 1     # 그늘 적중
            elif obs_sun and not pred_sun:     arr[1] += 1     # 양지를 그늘이라 함
            elif not obs_sun and pred_sun:     arr[2] += 1     # 그늘을 양지라 함
            else:                              arr[3] += 1     # 양지 적중
        if (ds_a >= THR) != (ds_b >= THR):
            changed.append((lat, lon, "양지" if obs_sun else "그늘", round(tf, 2)))

    print(f"실측 {len(rows)}지점  (양지 {sum(1 for r in rows if str(r['볕']).strip()=='1')} / "
          f"그늘 {sum(1 for r in rows if str(r['볕']).strip()!='1')})\n")
    mat(*a, "건물만")
    print()
    mat(*b, "건물 + 가로수")
    print()
    if changed:
        print(f"가로수로 판정이 바뀐 {len(changed)}지점 (좌표, 실측, 차광률):")
        for la, lo, o, t in changed:
            ok = "맞음" if o == "그늘" else "틀림"
            print(f"   {la:.5f},{lo:.5f}  실측={o}  tree_f={t}  → {ok}")
    else:
        print("가로수로 판정이 바뀐 지점 없음.")
        print("(실측지 대부분이 무수목이면 이것이 정상이다 — 이 경우 80지점으로는")
        print(" 가로수 층을 검증할 수 없고, 수목 가로에서 별도 실측이 필요하다.)")


if __name__ == "__main__":
    asyncio.run(main())
