#!/usr/bin/env python3
"""건물 높이 실태 + 그늘 판정 A/B/C (2026-09-14).

왜 필요한가:
  그늘 21곳 중 19곳을 양지라고 부르고 있다(kappa 0.06). 지금까지는 좌표 오차 탓으로 봤는데,
  코드를 다시 읽어보니 두 가지가 더 있다.

  ① sun_blocked_outdoor 는 층수를 모르는 건물을 **그림자 계산에서 통째로 뺀다**.
     "그림자 지어내기 금지"라는 원칙은 옳지만, 결과는 한쪽으로만 틀린다 — 그늘을 놓친다.
     svf_geometric 은 이미 2026-09-08 에 결측을 2층으로 채우도록 고쳤다(bias +0.088 -> +0.002).
     같은 수정을 그늘 판정에는 하지 않았다.
     부산 GIS건물통합 층수 보유율 61%, 표제부 조인 후에도 결측 21.2%.
     부암동은 조인 후에도 63/241 결측이고, 하필 SVF r 이 0.00 인 동네다.

  ② 그늘 판정은 아직 '건물 중심 방위 ± 각폭' 근사를 쓴다. svf_geometric 은 같은 날
     "중심±각폭 근사(과차폐)를 버리고 모서리까지 정확 거리를 씀"으로 바꿨는데 여기는 안 바꿨다.
     가장 가까운 모서리 거리와 전체 각폭을 짝지어 쓰므로 기하가 일관되지 않다.

무엇을 재는가:
  1부  80점 반경 안 건물의 높이 출처별 개수.
       height(m) 있음 / 층수만 있음 / 둘 다 없음.
       셋째가 적으면 3D 건물 데이터를 받을 이유가 없고, 많으면 받아야 한다.
  2부  같은 80점에서 그늘 판정 A/B/C 를 kappa 로 비교.
       A 현행        결측 버림 + 중심±각폭
       B 결측 2층    + 중심±각폭
       C 결측 2층    + 정확 광선투사
       B 가 오르면 원인은 데이터 결측, C 가 더 오르면 기하 근사까지가 원인이다.
       둘 다 안 오르면 좌표 오차가 맞고, 그때 비로소 좌표 쪽에 투자하면 된다.

주의: 이 표는 배포된 엔진을 그대로 쓴다. GEO_USE_GRID=1 이면 스카이라인 격자가 먼저 답해
      A/B/C 가 전부 같아진다 — 그 경우 경고를 찍는다.

  docker cp scripts/shade_height_ab.py climax-api:/tmp/
  docker cp data/tier3_engine_output_80_v6.csv climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/shade_height_ab.py
"""
from __future__ import annotations
import asyncio
import csv
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, "/app")

from vpti_core import DEFAULT_CONFIG                 # noqa: E402
from vpti_core.solar import estimate_solar           # noqa: E402
from app.services import geo                         # noqa: E402

IN = "/tmp/tier3_engine_output_80_v6.csv"
OUT = "/tmp/shade_height_ab.json"
EYE = 1.5
DEFAULT_FLOORS = 2


# ── 1부: 높이 출처 ────────────────────────────────────────────────
def height_source(props: dict) -> str:
    """이 건물이 그림자를 만들 수 있는가, 무엇으로 만드는가."""
    h = props.get("height")
    try:
        if h is not None and float(h) > 0:
            return "height_m"          # 실제 미터 높이 — 가장 좋다
    except (TypeError, ValueError):
        pass
    for k in ("gro_flo_co", "building:levels"):
        try:
            if int(props.get(k) or 0) > 0:
                return "levels"        # 층수 x 2.8 근사
        except (TypeError, ValueError):
            continue
    return "none"                      # 현행 코드에서는 투명 건물


# ── 2부: 그늘 판정 세 방식 ────────────────────────────────────────
def _blocked_angular(rings, az: float, el: float, default_floors) -> tuple[bool, str]:
    """A/B 공용 — 현행 sun_blocked_outdoor 와 같은 중심±각폭 근사."""
    outside = [(r, p) for r, p in rings if not geo._point_in_ring(0.0, 0.0, r)]
    for n in geo._collect_neighbors(outside, home_ring=None,
                                    default_floors=default_floors):
        d_az = abs(((az - n.az_deg + 180) % 360) - 180)
        if d_az > n.half_deg + 2.0:
            continue
        rise = n.height_m - EYE
        if rise <= 0:
            continue
        if el < math.degrees(math.atan2(rise, n.dist_m)):
            return True, n.label
    return False, ""


def _blocked_raycast(rings, az: float, el: float, default_floors) -> tuple[bool, str]:
    """C — svf_geometric 과 같은 기하. 태양 방위로 광선을 쏴 실제 교차거리를 쓴다.

    중심±각폭은 건물을 원호로 뭉갠다. 긴 건물은 각폭이 넓어 실제로는 비껴가는 태양까지
    가렸다고 하고, 반대로 모서리 근처는 가리는데 각폭 밖이라 놓친다. 광선은 그 둘을 안 한다.
    """
    dx, dy = math.sin(math.radians(az)), math.cos(math.radians(az))
    for ring, props in rings:
        if len(ring) < 4 or geo._point_in_ring(0.0, 0.0, ring):
            continue
        H = geo._height_m_from_props(props, default_floors=default_floors)
        if H is None:
            continue
        d = geo._ray_ring_hit(dx, dy, ring)
        if d is None or d < 1.0:
            continue
        rise = H - EYE
        if rise <= 0:
            continue
        if el < math.degrees(math.atan2(rise, d)):
            label = str(props.get("buld_nm_dc") or props.get("buld_nm")
                        or props.get("name") or "이웃 건물")
            return True, label
    return False, ""


# ── 채점 ──────────────────────────────────────────────────────────
def score(pairs, label):
    """pairs: (실측이 양지인가, 예측이 양지인가). '그늘'을 양성으로 채점."""
    tp = sum(1 for o, p in pairs if not o and not p)      # 그늘 적중
    fp = sum(1 for o, p in pairs if o and not p)          # 양지를 그늘이라 함
    fn = sum(1 for o, p in pairs if not o and p)          # 그늘을 양지라 함
    tn = sum(1 for o, p in pairs if o and p)
    n = tp + fp + fn + tn
    po = (tp + tn) / n
    pe = ((tp + fn) * (tp + fp) + (fp + tn) * (fn + tn)) / (n * n)
    kap = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    print(f"  {label:26} 정확도 {po*100:5.1f}%   kappa {kap:+.3f}   "
          f"그늘적중 {tp:2d}/{tp+fn:2d}   오경보 {fp:2d}/{fp+tn:2d}")
    return dict(kappa=round(kap, 3), acc=round(po, 3), tp=tp, fp=fp, fn=fn, tn=tn)


async def main() -> None:
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    obs_sun = [str(r["볕"]).strip() == "1" for r in rows]
    print(f"\n입력 {len(rows)}행  양지 {sum(obs_sun)} / 그늘 {len(rows)-sum(obs_sun)}")
    grid = os.environ.get("GEO_USE_GRID", "0")
    print(f"GEO_USE_GRID={grid}"
          + ("   ⚠️ 격자가 먼저 답하면 A/B/C 가 같아진다" if grid == "1" else ""))

    # 태양 위치 + 폴리곤을 한 번만 받아 세 방식에 공유한다.
    per_point, rings_all, sol_all = [], [], []
    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")   # naive = KST
        s = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
        sol_all.append((s.solar_azimuth_deg, s.solar_elevation_deg))
        rings, src = await geo._rings_cached(lat, lon)
        rings_all.append(rings)
        c = Counter(height_source(p) for _r, p in rings)
        per_point.append(dict(name=r.get("지점명", ""), src=src, n=len(rings), **c))

    # ── 1부 ──
    tot = Counter()
    for d in per_point:
        for k in ("height_m", "levels", "none"):
            tot[k] += d.get(k, 0)
    N = sum(tot.values())
    print(f"\n=== 1. 80점 주변 건물 {N:,}동의 높이 출처 ===")
    if N:
        print(f"  실제 높이(m) 있음   {tot['height_m']:6,}  ({100*tot['height_m']/N:5.1f} %)")
        print(f"  층수만 있음         {tot['levels']:6,}  ({100*tot['levels']/N:5.1f} %)  x 2.8m 근사")
        print(f"  둘 다 없음          {tot['none']:6,}  ({100*tot['none']/N:5.1f} %)  <- 현행에선 투명")
    src_c = Counter(d["src"] for d in per_point)
    print(f"  폴리곤 출처: {dict(src_c)}")

    worst = sorted(per_point, key=lambda d: -(d.get("none", 0) / max(d["n"], 1)))[:8]
    print("\n  결측 비율이 높은 지점 (상위 8):")
    for d in worst:
        pct = 100 * d.get("none", 0) / max(d["n"], 1)
        print(f"    {d['name'][:14]:<16} 건물 {d['n']:3d}  결측 {d.get('none',0):3d} ({pct:4.0f}%)")

    zero = [d for d in per_point if d["n"] == 0]
    if zero:
        print(f"\n  ⚠️ 주변 건물이 0동인 지점 {len(zero)}개 — 폴리곤 자체를 못 받았다:")
        for d in zero:
            print(f"    {d['name'][:14]}")

    # ── 2부 ──
    print("\n=== 2. 그늘 판정 A/B/C ===")
    A, B, C = [], [], []
    flips = []
    for i, rings in enumerate(rings_all):
        az, el = sol_all[i]
        if el <= 0.0:
            A.append((obs_sun[i], True)); B.append((obs_sun[i], True)); C.append((obs_sun[i], True))
            continue
        ba, _ = _blocked_angular(rings, az, el, None)              # A 현행
        bb, _ = _blocked_angular(rings, az, el, DEFAULT_FLOORS)    # B 결측 2층
        bc, lc = _blocked_raycast(rings, az, el, DEFAULT_FLOORS)   # C 광선투사
        A.append((obs_sun[i], not ba))
        B.append((obs_sun[i], not bb))
        C.append((obs_sun[i], not bc))
        if not (ba == bb == bc):
            flips.append((per_point[i]["name"][:14],
                          "양지" if obs_sun[i] else "그늘",
                          "그늘" if ba else "양지",
                          "그늘" if bb else "양지",
                          "그늘" if bc else "양지", lc[:16], el))

    sA = score(A, "A 현행 (결측 버림)")
    sB = score(B, "B 결측 2층")
    sC = score(C, "C 결측 2층 + 광선투사")

    print()
    if sC["kappa"] > sA["kappa"] + 0.05 or sB["kappa"] > sA["kappa"] + 0.05:
        print("  -> 코드·데이터가 원인이다. 좌표 오차 탓이 아니었다.")
        if sC["kappa"] > sB["kappa"] + 0.03:
            print("     기하 근사(중심±각폭)까지 고쳐야 한다. C 를 채택.")
        else:
            print("     결측 채움만으로 충분하다. B 를 채택.")
    else:
        print("  -> 셋이 비슷하다. 결측·기하가 원인이 아니므로 좌표 오차와 차양 쪽이 남는다.")
    print("     (오경보가 함께 늘었는지 볼 것 — 그늘을 더 많이 부르면 적중도 늘지만 양지를 틀린다.)")

    if flips:
        print(f"\n  세 방식이 갈린 {len(flips)}지점:")
        print(f"    {'지점':<16}{'실측':<6}{'A':<6}{'B':<6}{'C':<6}{'C의 근거':<18}태양고도")
        for nm, o, a, b, c, lab, el in flips:
            mark = ""
            if c == o and a != o:
                mark = "  <- C가 맞음"
            elif a == o and c != o:
                mark = "  <- C가 틀림"
            print(f"    {nm:<16}{o:<6}{a:<6}{b:<6}{c:<6}{lab:<18}{el:4.0f}°{mark}")

    json.dump(dict(generated="2026-09-14", n=len(rows),
                   height_sources=dict(tot), per_point=per_point,
                   A=sA, B=sB, C=sC),
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
