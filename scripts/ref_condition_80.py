#!/usr/bin/env python3
"""80지점을 **같은 기상·같은 태양 위치**에 놓고 엔진으로 다시 계산 (2026-09-21).

왜 필요한가
  근린마다 다른 날·다른 시각에 쟀다. 특히 부암제1동만 14:10~15:06(태양고도 46~56°)이고
  나머지 넷은 정오 전후(62~67°)다. PET 실측값을 그대로 나란히 놓으면 「부암은 그늘이
  시원하지 않다」가 LCZ 1 의 성질처럼 읽히지만, 실제로는 **측정 시각이 달라서**일 수 있다.
  회귀로 보정하려 해도 부암 말고는 46~56° 구간 자료가 없어 15~18° 외삽이 되고,
  추정자에 따라 보정치가 6 K 에서 11 K 까지 흔들린다 — 논문에 쓸 수 없다.

무엇을 하는가
  지점의 **형태(SVF·GVI)와 볕/그늘만 그대로 두고**, 기상과 태양 위치를 하나로 고정해
  배포 엔진(compute_vpti_thermal → PET)으로 다시 계산한다. 그러면 근린 간 차이는
  「언제 쟀는가」가 아니라 **「어떻게 생겼는가」**에서만 온다.

기준 조건
  2026-08-23 12:30 KST (부산 태양고도 ≈ 65°), Ta 35.0 °C, RH 45 %, 바람 1.0 m/s, 운량 0.
  형태 입력은 9/14 재산출 어안 지표(aug80_indices.csv 의 SVF·GVI·BVI) — 정본.

출력: data/ref_condition_80.csv (측정ID, 권역, 볕, SVF, GVI, ref_Tmrt, ref_PET)
"""
from __future__ import annotations
import csv, sys
from datetime import datetime

sys.path.insert(0, "backend")
from vpti_core import DEFAULT_CONFIG
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet

REF_WHEN = datetime(2026, 8, 23, 12, 30, 0)
REF_TA, REF_RH, REF_V, REF_CLOUD = 35.0, 45.0, 1.0, 0.0

MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]


def views(svf, gvi, bvi):
    """실측 세 지표를 모두 넣는다. (2026-09-21 수정: 예전엔 건물 비율을 1−SVF 로
    추정해 넣었다 — 배포 엔진 스칼라 입력의 관행. 실측 BVI 가 있으므로 그걸 쓴다.)
    엔진에서 BVI 는 복사(MRT)에는 안 들어가고 보행 풍속(PWI) 감쇠에만 쓰인다."""
    g = max(0.0, min(1.0, gvi))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    b = max(0.0, min(bvi, 1.0 - sky_h - g))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=g,
                            building_ratio=b) for d in ("front", "back", "left", "right")]
    return vs


def main():
    meas = {r["측정ID"]: r for r in csv.DictReader(
        open("data/tier3_engine_output_80_v7_photo.csv", encoding="utf-8-sig"))}
    idx = list(csv.DictReader(open("data/aug80_indices.csv", encoding="utf-8-sig")))

    out = []
    for r in idx:
        mid = r["측정ID"]
        m = meas.get(mid)
        if not m:
            continue
        lat, lon = float(m["위도"]), float(m["경도"])
        svf, gvi, bvi = float(r["SVF"]), float(r["GVI"]), float(r["BVI"])
        shade = 1.0 if str(m["볕"]).strip() == "1" else 0.0
        wc = WeatherContext(temperature_c=REF_TA, humidity_pct=REF_RH,
                            wind_speed_ms=REF_V, wind_direction_deg=0.0)
        res = compute_vpti_thermal(views_5=views(svf, gvi, bvi), materials=MATS, weather=wc,
                                   road_axis_deg=0.0, lat=lat, lon=lon, when=REF_WHEN,
                                   direct_shade=shade, wind_is_pedestrian=False,
                                   cloud_fraction=REF_CLOUD)
        tmrt = float(res.mrt.tmrt)
        pet = compute_pet(tdb=REF_TA, tr=tmrt, v=res.pedestrian_wind_ms, rh=REF_RH,
                          season=res.season, config=DEFAULT_CONFIG.comfort)
        out.append(dict(측정ID=mid, 권역=m["권역"], 볕=m["볕"], SVF=f"{svf:.4f}",
                        GVI=f"{gvi:.4f}", BVI=f"{bvi:.4f}", ref_Tmrt=f"{tmrt:.2f}",
                        ref_PET=f"{float(pet.value):.2f}"))

    with open("data/ref_condition_80.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"wrote data/ref_condition_80.csv  n={len(out)}")

    import statistics as st
    from collections import defaultdict
    g = defaultdict(list)
    for r in out:
        g[r["권역"]].append(r)
    print(f"\n기준조건 {REF_WHEN:%Y-%m-%d %H:%M} · Ta {REF_TA} · RH {REF_RH} · "
          f"v {REF_V} · cloud {REF_CLOUD}")
    print(f"{'권역':10s} {'볕n':>3s} {'볕PET':>7s} {'그늘n':>4s} {'그늘PET':>7s} {'Δ':>6s}")
    for k, v in g.items():
        s = [float(x["ref_PET"]) for x in v if x["볕"] == "1"]
        h = [float(x["ref_PET"]) for x in v if x["볕"] != "1"]
        ms = st.median(s) if s else float("nan")
        mh = st.median(h) if h else float("nan")
        print(f"{k:10s} {len(s):3d} {ms:7.2f} {len(h):4d} {mh:7.2f} {ms - mh:6.2f}")


if __name__ == "__main__":
    main()
