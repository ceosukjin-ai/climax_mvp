#!/usr/bin/env python3
"""부드러운 그늘 — 위치 불확실성을 직달광에 전파한다 (2026-09-13).

왜:
  그늘 21지점의 태양고도 중앙값이 64도다. 그 고도에서 3층 건물이 만드는 그늘은
  반경 3.7 m 이고 GPS 오차는 5~10 m 다. 그래서 그늘 여부가 위치 오차 안에서
  결정된다. 실제로 12 m 원판을 훑어 보니 21곳 중

      4곳  주변의 44~60 % 가 그늘  -> 엔진이 맞힌 곳이 정확히 이 4곳이다
     10곳  주변의  8~32 % 가 그늘  -> 그늘은 있는데 자리를 못 짚었다
      7곳  주변에 그늘이 없다        -> 차양·간판류. 건물 폴리곤에 없다

  즉 엔진은 "원판의 절반이 그늘일 때만" 맞힌다. 그런데 이분법으로 0/1 을 내는 것은
  ±8 m 로만 아는 위치에 대해 없는 확신을 지어내는 것이다. 원판의 그늘 비율을 그대로
  직달광 감쇠에 쓰면, 32 % 그늘인 자리는 직달광의 32 % 가 깎인다.

  compute_vpti_thermal 은 이미 direct_shade 를 0~1 연속값으로 받는다. 바꿀 곳은
  그 값을 만드는 한 줄뿐이다.

비교 대상 (같은 80점, 같은 기상, 같은 벽 설정 = 배포 상태):
  S-obs    실측 볕/그늘            (상한. 사람이 옆에서 알려준 경우)
  S-bin    엔진 이분법             (현행 배포)
  S-soft   원판 그늘비율 (반경 여러 개)

  docker exec -i climax-api python3 /tmp/run_soft_shade_80.py
"""
from __future__ import annotations
import asyncio
import csv
import math
import statistics
import sys
from datetime import datetime

sys.path.insert(0, "/app")
sys.path.insert(0, "/repo/backend")

from vpti_core import DEFAULT_CONFIG                      # noqa: E402
from vpti_core.solar import estimate_solar                # noqa: E402
from vpti_core.vsi import ViewSegmentation                # noqa: E402
from vpti_core.smti import MaterialFraction               # noqa: E402
from vpti_core.vpti import WeatherContext, compute_vpti_thermal   # noqa: E402
from vpti_core.comfort import compute_pet                 # noqa: E402
from app.services import geo                              # noqa: E402
from app.services.geo import dominant_wall_material       # noqa: E402

IN = "/tmp/tier3_engine_output_80_v6.csv"
MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]
RADII = [6.0, 10.0, 14.0]          # 훑을 반경 m — GPS 오차 가정
RINGS, SPOKES = 2, 8               # 표본 = 1 + RINGS*SPOKES = 17


def views(svf: float, gvi: float):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi,
                            building_ratio=b) for d in ("front", "back", "left", "right")]
    return vs


async def shade_fraction(lat0: float, lon0: float, az: float, el: float,
                         radius_m: float, rings: int = RINGS,
                         spokes: int = SPOKES) -> float:
    """위치 오차 반경 안에서 태양이 가려지는 위치의 비율 0~1."""
    if el <= 0.0:
        return 1.0
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians(lat0))
    offs = [(0.0, 0.0)]
    for i in range(1, rings + 1):
        rad = radius_m * i / rings
        for j in range(spokes):
            th = 2 * math.pi * j / spokes
            offs.append((rad * math.cos(th), rad * math.sin(th)))
    hit = 0
    for dx, dy in offs:
        blocked, _ = await geo.sun_blocked_outdoor(
            lat0 + dy / mlat, lon0 + dx / mlon, az, el)
        if blocked:
            hit += 1
    return hit / len(offs)


def pet_of(r, direct_shade: float, alb: float | None) -> tuple[float, float]:
    """(흑구 예측 PET, 흑구 예측 Tmrt). 벽 스테이지는 배포 상태대로 끈다."""
    lat, lon = float(r["위도"]), float(r["경도"])
    when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")   # naive = KST
    ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"])
    svf, gvi = float(r["tier3_svf"]), float(r["tier3_gvi"])
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v,
                        wind_direction_deg=0.0)
    res = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc,
                               road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                               direct_shade=direct_shade, wall_temp_c=None,
                               wall_albedo=alb, wind_is_pedestrian=True)
    tg = float(res.mrt.tmrt_globe)
    pet = float(compute_pet(tdb=ta, tr=tg, v=res.pedestrian_wind_ms, rh=rh,
                            season=res.season, config=DEFAULT_CONFIG.comfort).value)
    return pet, tg


def score(label, pred, truth, sun):
    e = [p - t for p, t in zip(pred, truth)]
    n = len(e)
    mae = sum(map(abs, e)) / n
    bias = sum(e) / n
    mp, mt = statistics.mean(pred), statistics.mean(truth)
    sp = math.sqrt(sum((p - mp) ** 2 for p in pred) / n)
    st = math.sqrt(sum((t - mt) ** 2 for t in truth) / n)
    r = (sum((pred[i] - mp) * (truth[i] - mt) for i in range(n)) / n / (sp * st)
         if sp > 0 and st > 0 else float("nan"))
    hot = [i for i in range(n) if truth[i] >= 41]
    hit = sum(1 for i in hot if pred[i] >= 41)
    si = [i for i in range(n) if sun[i]]
    hi = [i for i in range(n) if not sun[i]]
    mae_s = sum(abs(e[i]) for i in si) / len(si)
    mae_h = sum(abs(e[i]) for i in hi) / len(hi)
    bias_h = sum(e[i] for i in hi) / len(hi)
    print(f"  {label:22} MAE {mae:5.2f}  bias {bias:+5.2f}  r {r:5.2f}  "
          f"극심 {hit}/{len(hot)}   양지MAE {mae_s:4.2f}  그늘MAE {mae_h:4.2f} "
          f"(bias {bias_h:+5.2f})")
    return mae


async def main() -> None:
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    truth = [float(r["PET"]) for r in rows]
    sun = [str(r["볕"]).strip() == "1" for r in rows]
    print(f"입력 {len(rows)}행  {IN}")
    print(f"양지 {sum(sun)} / 그늘 {len(rows)-sum(sun)}   "
          f"극심(PET>=41) {sum(1 for t in truth if t >= 41)}\n")

    # 좌표별 외벽 알베도 (배포 상태: 단파반사 ON, 벽온도 스테이지 OFF)
    cache: dict[tuple, dict] = {}
    albs = []
    for r in rows:
        k = (round(float(r["위도"]), 5), round(float(r["경도"]), 5))
        if k not in cache:
            cache[k] = await dominant_wall_material(k[0], k[1])
        albs.append(cache[k]["albedo"])

    # 태양 위치 + 이분법 판정 + 반경별 그늘비율
    az_el, binr, frac = [], [], {rd: [] for rd in RADII}
    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
        sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
        az, el = sol.solar_azimuth_deg, sol.solar_elevation_deg
        az_el.append((az, el))
        blocked, _ = await geo.sun_blocked_outdoor(lat, lon, az, el)
        binr.append(0.0 if blocked else 1.0)
        for rd in RADII:
            frac[rd].append(await shade_fraction(lat, lon, az, el, rd))

    print("=== 그늘 비율 요약 ===")
    for rd in RADII:
        f = frac[rd]
        fs = [f[i] for i in range(len(rows)) if sun[i]]
        fh = [f[i] for i in range(len(rows)) if not sun[i]]
        print(f"  반경 {rd:4.0f} m   실측양지 평균 {statistics.mean(fs):.3f}   "
              f"실측그늘 평균 {statistics.mean(fh):.3f}   "
              f"차이 {statistics.mean(fh)-statistics.mean(fs):+.3f}")
    print()

    print("=== 실측 PET 대비 (흑구 기준) ===")
    obs = [1.0 if s else 0.0 for s in sun]
    for label, ds in ([("S-obs  실측 볕/그늘", obs), ("S-bin  엔진 이분법", binr)]
                      + [(f"S-soft 반경 {rd:.0f} m", [1.0 - x for x in frac[rd]])
                         for rd in RADII]):
        pred = [pet_of(rows[i], ds[i], albs[i])[0] for i in range(len(rows))]
        score(label, pred, truth, sun)

    print()
    print("읽는 법:")
    print("  · S-soft 가 S-bin 보다 나으면 이분법을 버릴 근거가 된다.")
    print("  · S-obs 는 사람이 옆에서 알려준 상한이다. S-soft 가 그 사이 어디에 오는지를 본다.")
    print("  · 그늘MAE 와 그늘bias 를 특히 볼 것 — 지금 엔진은 그늘을 양지로 봐서 과대추정한다.")


if __name__ == "__main__":
    asyncio.run(main())
