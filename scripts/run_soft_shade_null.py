#!/usr/bin/env python3
"""부드러운 그늘의 대조 실험 — 그냥 상수로 깎아도 같아지는가 (2026-09-13).

왜 필요한가:
  run_soft_shade_80.py 에서 S-soft(반경 14 m) 가 S-bin 보다 MAE 6.20 -> 5.60 으로
  좋아졌다. 그런데 개선이 **양지**(5.48 -> 4.81)에서 나왔고 그늘은 그대로였다
  (8.24 -> 7.83, bias +7.27 -> +7.35). 실측 양지 지점의 평균 그늘비율이 0.133 이므로
  이 처리는 사실상 모든 점에 DNI x 0.87 을 곱한 것과 같다. 엔진 전체가 +5 만큼
  과대추정하고 있으니 무엇으로든 깎으면 MAE 는 준다.

  그래서 위치와 무관한 상수 감쇠를 같은 표에 올린다. 상수가 같거나 더 나으면
  부드러운 그늘은 효과가 아니라 우연이고, 채택하면 안 된다.

  N-const c : direct_shade = 1 - c  (모든 점에 동일)
  S-soft    : direct_shade = 1 - 그늘비율(반경 14 m)

  docker exec -i climax-api python3 /tmp/run_soft_shade_null.py
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
RADIUS, RINGS, SPOKES = 14.0, 2, 8
CONSTS = [0.00, 0.05, 0.10, 0.13, 0.20, 0.30]


def views(svf: float, gvi: float):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi,
                            building_ratio=b) for d in ("front", "back", "left", "right")]
    return vs


async def shade_fraction(lat0, lon0, az, el, radius_m=RADIUS):
    if el <= 0.0:
        return 1.0
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians(lat0))
    offs = [(0.0, 0.0)]
    for i in range(1, RINGS + 1):
        rad = radius_m * i / RINGS
        for j in range(SPOKES):
            th = 2 * math.pi * j / SPOKES
            offs.append((rad * math.cos(th), rad * math.sin(th)))
    hit = 0
    for dx, dy in offs:
        blocked, _ = await geo.sun_blocked_outdoor(
            lat0 + dy / mlat, lon0 + dx / mlon, az, el)
        if blocked:
            hit += 1
    return hit / len(offs)


def pet_of(r, direct_shade, alb):
    lat, lon = float(r["위도"]), float(r["경도"])
    when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
    ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"])
    svf, gvi = float(r["tier3_svf"]), float(r["tier3_gvi"])
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v,
                        wind_direction_deg=0.0)
    res = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc,
                               road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                               direct_shade=direct_shade, wall_temp_c=None,
                               wall_albedo=alb, wind_is_pedestrian=True)
    tg = float(res.mrt.tmrt_globe)
    return float(compute_pet(tdb=ta, tr=tg, v=res.pedestrian_wind_ms, rh=rh,
                             season=res.season, config=DEFAULT_CONFIG.comfort).value)


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
    print(f"  {label:20} MAE {mae:5.2f}  bias {bias:+5.2f}  r {r:5.2f}  "
          f"극심 {hit}/{len(hot)}   양지MAE {sum(abs(e[i]) for i in si)/len(si):4.2f}  "
          f"그늘MAE {sum(abs(e[i]) for i in hi)/len(hi):4.2f} "
          f"(bias {sum(e[i] for i in hi)/len(hi):+5.2f})")
    return mae, r


async def main() -> None:
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    truth = [float(r["PET"]) for r in rows]
    sun = [str(r["볕"]).strip() == "1" for r in rows]

    cache, albs = {}, []
    for r in rows:
        k = (round(float(r["위도"]), 5), round(float(r["경도"]), 5))
        if k not in cache:
            cache[k] = await dominant_wall_material(k[0], k[1])
        albs.append(cache[k]["albedo"])

    binr, soft = [], []
    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
        sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
        az, el = sol.solar_azimuth_deg, sol.solar_elevation_deg
        blocked, _ = await geo.sun_blocked_outdoor(lat, lon, az, el)
        binr.append(0.0 if blocked else 1.0)
        soft.append(1.0 - await shade_fraction(lat, lon, az, el))

    print(f"\n입력 {len(rows)}행   양지 {sum(sun)} / 그늘 {len(rows)-sum(sun)}\n")
    print("=== 실측 PET 대비 (흑구 기준) ===")
    obs = [1.0 if s else 0.0 for s in sun]
    score("S-obs  실측", [pet_of(rows[i], obs[i], albs[i]) for i in range(len(rows))],
          truth, sun)
    m_bin, _ = score("S-bin  이분법",
                     [pet_of(rows[i], binr[i], albs[i]) for i in range(len(rows))],
                     truth, sun)
    m_soft, r_soft = score(f"S-soft 반경 {RADIUS:.0f} m",
                           [pet_of(rows[i], soft[i], albs[i]) for i in range(len(rows))],
                           truth, sun)
    print()
    best_c, best_m, best_r = None, 1e9, 0.0
    for c in CONSTS:
        m, r = score(f"N-const  {c:.2f}",
                     [pet_of(rows[i], 1.0 - c, albs[i]) for i in range(len(rows))],
                     truth, sun)
        if m < best_m:
            best_c, best_m, best_r = c, m, r
    print()
    print(f"최선의 상수 c = {best_c:.2f}   MAE {best_m:.2f}  r {best_r:.2f}")
    print(f"부드러운 그늘            MAE {m_soft:.2f}  r {r_soft:.2f}")
    print()
    if m_soft < best_m - 0.10:
        print("  부드러운 그늘이 상수보다 확실히 낫다 -> 위치 정보가 실제로 쓰인다. 채택 검토.")
    elif abs(m_soft - best_m) <= 0.10:
        print("  상수와 사실상 같다 -> 개선은 '깎았기 때문'이지 '그늘을 알았기 때문'이 아니다.")
        print("  부드러운 그늘을 채택하면 안 된다. 대신 엔진의 +bias 자체를 봐야 한다.")
    else:
        print("  상수가 더 낫다 -> 부드러운 그늘은 폐기. 감쇠 상수 교정이 먼저다.")
    print()
    print("주의: 상수 감쇠는 그 자체로 채택 대상이 아니다. 80점에 맞춘 값이라")
    print("      다른 계절·도시에서 무너진다. 이 표의 용도는 '부드러운 그늘의 개선이")
    print("      진짜인가'를 가르는 것뿐이다.")


if __name__ == "__main__":
    asyncio.run(main())
