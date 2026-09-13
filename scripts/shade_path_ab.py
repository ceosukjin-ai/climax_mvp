#!/usr/bin/env python3
"""그늘 판정 A/B — 스카이라인 격자 vs 건물 폴리곤 (2026-09-13).

왜:
  sun_blocked_outdoor 는 스카이라인 격자 칸이 있으면 거기서 즉시 반환하고
  건물 폴리곤을 보지 않는다. 격자는 0.0002도 ≈ 22 m 칸이다. SVF 는 이미
  양자화 손실(0.121 -> 0.153, r 붕괴) 때문에 앱 경로에서 격자를 뺐는데,
  그늘 판정은 아직 격자를 쓴다. 22 m 칸 하나가 볕/그늘을 대표할 수 있는지
  실측 80지점으로 직접 가른다.

  A = 현행 (격자 우선)
  B = 격자 우회 (get_cell 을 None 으로 만들어 폴리곤 경로 강제)

쓰는 법 (컨테이너 안):
  docker exec -i climax-api python3 /tmp/shade_path_ab.py /tmp/pts80.csv
"""
from __future__ import annotations
import asyncio
import csv
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")


def score(pairs, label):
    """pairs: (실측양지, 예측양지) 목록. '그늘'을 양성으로 채점한다."""
    tp = sum(1 for o, p in pairs if not o and not p)      # 그늘 적중
    fp = sum(1 for o, p in pairs if o and not p)          # 양지를 그늘이라 함
    fn = sum(1 for o, p in pairs if not o and p)          # 그늘을 양지라 함
    tn = sum(1 for o, p in pairs if o and p)
    n = tp + fp + fn + tn
    po = (tp + tn) / n
    pe = ((tp + fn) * (tp + fp) + (fp + tn) * (fn + tn)) / (n * n)
    kap = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    print(f"{label:22} 정확도 {po*100:5.1f}%   kappa {kap:+.3f}   "
          f"그늘 {tp}/{tp+fn}   오경보 {fp}")
    return kap


async def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pts80.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))

    from vpti_core.solar import estimate_solar
    from app.services import skyline as sky
    from app.services import geo

    a_pairs, b_pairs, disagree = [], [], []
    n_cell = 0

    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
        obs_sun = str(r["볕"]).strip() == "1"
        sol = estimate_solar(lat, lon, when)
        az, el = sol.solar_azimuth_deg, sol.solar_elevation_deg

        cell = await sky.get_cell(lat, lon)
        if cell is not None:
            n_cell += 1

        blocked_a, why_a = await geo.sun_blocked_outdoor(lat, lon, az, el)

        # B: 격자를 못 찾은 것처럼 만들어 폴리곤 경로를 강제한다
        orig = sky.get_cell

        async def _none(*_a, **_k):
            return None

        sky.get_cell = _none
        try:
            blocked_b, why_b = await geo.sun_blocked_outdoor(lat, lon, az, el)
        finally:
            sky.get_cell = orig

        a_pairs.append((obs_sun, not blocked_a))
        b_pairs.append((obs_sun, not blocked_b))
        if blocked_a != blocked_b:
            disagree.append((r.get("지점명", ""), lat, lon,
                             "양지" if obs_sun else "그늘",
                             "그늘" if blocked_a else "양지",
                             "그늘" if blocked_b else "양지",
                             why_a or why_b or ""))

    print(f"\n실측 {len(rows)}지점 "
          f"(양지 {sum(1 for r in rows if str(r['볕']).strip()=='1')} / "
          f"그늘 {sum(1 for r in rows if str(r['볕']).strip()!='1')})")
    print(f"스카이라인 격자 칸이 있는 지점: {n_cell}/{len(rows)}\n")
    ka = score(a_pairs, "A 현행 (격자 우선)")
    kb = score(b_pairs, "B 격자 우회 (폴리곤)")
    print()
    if kb > ka + 0.02:
        print("  격자를 빼면 나아진다. 그늘 판정도 SVF 처럼 격자에서 내려야 한다.")
    elif ka > kb + 0.02:
        print("  격자가 더 낫다. 폴리곤 경로(층수 결측 버림)가 더 큰 문제다.")
    else:
        print("  둘이 거의 같다. 격자가 원인이 아니다 — 폴리곤 기하 자체를 봐야 한다.")

    if disagree:
        print(f"\n두 경로가 갈린 {len(disagree)}지점:")
        print(f"  {'지점':<16}{'실측':<6}{'A격자':<7}{'B폴리곤':<8}사유")
        for nm, la, lo, o, aa, bb, why in disagree:
            mark = "  <-- B가 맞음" if bb == o and aa != o else (
                   "  <-- A가 맞음" if aa == o and bb != o else "")
            print(f"  {nm[:14]:<16}{o:<6}{aa:<7}{bb:<8}{why[:22]}{mark}")


if __name__ == "__main__":
    asyncio.run(main())
