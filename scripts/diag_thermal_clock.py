#!/usr/bin/env python3
"""열화상 카메라 시계 오차를 세션별로 되찾는다 (2026-09-16).

발견: 2026-09-16 현장에서 HIKMICRO 시계가 **33분 늦게** 맞춰져 있었다.
  카메라 시각 14:26 / 실제 14:59. 열화상 4무리와 실측 4지점이 +33분에서 ±3분 안에 맞았다.

왜 중요한가: 열화상 짝을 **시각으로** 잡는다. 시각이 33분 틀리면 태양 방위가 약 8° 어긋나고,
  볕/그늘 경계에 있는 지점은 라벨이 뒤집힌다. 9/13 벽면온도 직접검증도 이 영향을 받는다.

33분은 시계가 흐른 게 아니라 **한 번 잘못 맞춰진 것**이다(드리프트는 한 달에 몇 분이다).
그렇다면 과거 세션도 같은 33분일 가능성이 크다 — 하지만 **확인할 수 있는 것을 가정하지 않는다.**

방법: 세션(날짜)마다, 열화상 촬영 시각 무리와 실측 기록 시각을 놓고
  오프셋을 -90~+90분으로 훑어 **짝의 평균 거리가 최소가 되는 오프셋**을 찾는다.
  최소가 뚜렷하면(차점과 충분히 벌어지면) 그 세션의 시계 오차다.

  python3 scripts/diag_thermal_clock.py ~/mnt/DCIM
"""
from __future__ import annotations
import glob, os, re, sys
from datetime import datetime

# 실측 시각 (분 단위). 8월은 scripts/validate_field.py 의 ROWS 에서 가져왔다.
FIELD = {
    "20260916": ["13:27", "13:35", "13:42", "13:53"],
    "20260820": ["12:33", "12:40", "12:46", "12:50", "12:55", "13:00", "13:04", "13:08",
                 "13:12", "13:16", "13:19", "13:23", "13:26", "13:30", "13:33", "13:36",
                 "13:39", "13:42"],
    "20260823": ["11:19", "11:22", "11:29", "11:34", "11:40", "11:43", "11:47", "11:50",
                 "11:53", "11:56", "12:00", "12:04", "12:07", "12:11", "12:14", "12:17", "12:27"],
    "20260825": ["12:06", "12:09", "12:12", "12:16", "12:18", "12:24", "12:27", "12:30",
                 "12:34", "12:36", "12:41", "12:43", "12:46", "12:51", "12:56"],
    "20260826": ["12:05", "12:09", "12:16", "12:19", "12:24", "12:28", "12:35", "12:40",
                 "12:44", "12:49", "12:54", "12:57", "13:01", "13:04", "13:09", "13:12",
                 "14:10", "14:13", "14:17", "14:20", "14:22", "14:25", "14:27", "14:32",
                 "14:39", "14:47", "14:50", "14:53", "14:57", "15:00", "15:03", "15:06"],
}
PAT = re.compile(r"HM(\d{8})(\d{2})(\d{2})(\d{2})")


def mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/mnt/DCIM")
    shots: dict[str, list[int]] = {}
    for f in glob.glob(os.path.join(root, "**", "HM*.jpeg"), recursive=True):
        b = os.path.basename(f)
        if ".VIS." in b:
            continue                     # 가시광 짝은 같은 시각이라 한 번만 센다
        m = PAT.match(b)
        if m:
            shots.setdefault(m.group(1), []).append(int(m.group(2)) * 60 + int(m.group(3)))
    if not shots:
        raise SystemExit(f"열화상을 못 찾았다: {root}")

    print(f"\n{'날짜':<10}{'열화상':>7}{'실측':>6}{'최적오프셋':>11}{'평균거리':>10}{'차점과차이':>11}")
    for day in sorted(shots):
        t = sorted(shots[day])
        ref = FIELD.get(day)
        if not ref:
            print(f"{day:<10}{len(t):>7}{'-':>6}   (실측 시각 없음 — FIELD 에 추가 필요)")
            continue
        r = [mins(x) for x in ref]
        scores = []
        for off in range(-90, 91):
            # 각 열화상에서 가장 가까운 실측까지의 거리 평균
            d = sum(min(abs(x + off - y) for y in r) for x in t) / len(t)
            scores.append((d, off))
        scores.sort()
        best_d, best_off = scores[0]
        # 차점: 최적에서 10분 이상 떨어진 오프셋 중 가장 좋은 것 (봉우리 폭 때문)
        second = min((d for d, o in scores if abs(o - best_off) >= 10), default=float("inf"))
        print(f"{day:<10}{len(t):>7}{len(r):>6}{best_off:>+10}분{best_d:>9.1f}분{second-best_d:>10.1f}분")

    print("\n읽는 법:")
    print("  · '최적오프셋' 이 그 세션에서 카메라에 더해야 할 분이다(+33 = 카메라가 33분 늦음).")
    print("  · '평균거리' 가 3분 안쪽이면 짝이 잘 맞은 것이다.")
    print("  · '차점과차이' 가 작으면(<2분) 봉우리가 뚜렷하지 않다 — 그 세션은 믿지 말 것.")
    print("  · 세션마다 오프셋이 같으면 → 한 번 잘못 맞춰진 것. 다르면 → 중간에 건드린 것이다.")


if __name__ == "__main__":
    main()
