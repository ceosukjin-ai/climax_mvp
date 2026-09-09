#!/usr/bin/env python3
"""run_E: run_C(실측기상+tier3 시계+현장풍속) + 벽온도 스테이지. 그늘 bias 개선 확인."""
import csv, math, sys
from datetime import datetime
sys.path.insert(0, ".")
from vpti_core import DEFAULT_CONFIG, estimate_solar
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet
from vpti_core.mrt import estimate_wall_temp, sky_emissivity
from run_tier3 import views, MATS

def run(r, mode):
    lat, lon = float(r["위도"]), float(r["경도"])
    when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
    shade = 1.0 if str(r["볕"]).strip() == "1" else 0.0
    ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"])
    svf, gvi = float(r["tier3_svf"]), float(r["tier3_gvi"])
    sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
    wall = None
    if mode != "C" and svf < 0.92 and sol.solar_elevation_deg > 0:
        epsk = sky_emissivity(ta, rh, sol.cloud_fraction, DEFAULT_CONFIG.mrt)
        a, e = 0.30, 0.90
        if mode == "E1":
            wall = estimate_wall_temp(ta, sol, a, e, 0.45, v, epsk, DEFAULT_CONFIG.mrt)
        else:  # E2: 방위별 — look 방향 d 가 보는 파사드 법선 = d+180
            FN = {"N": 180.0, "E": 270.0, "S": 0.0, "W": 90.0}
            wall = {}
            for d, fn in FN.items():
                cosf = max(0.0, math.cos(math.radians(sol.solar_azimuth_deg - fn)))
                wall[d] = estimate_wall_temp(ta, sol, a, e, cosf, v, epsk, DEFAULT_CONFIG.mrt)
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    res = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc, road_axis_deg=0.0,
                               lat=lat, lon=lon, when=when, direct_shade=shade, wall_temp_c=wall,
                               wind_is_pedestrian=True)
    tm = float(res.mrt.tmrt)
    pet = float(compute_pet(tdb=ta, tr=tm, v=res.pedestrian_wind_ms, rh=rh, season=res.season,
                            config=DEFAULT_CONFIG.comfort).value)
    return tm, pet, (None if wall is None else (wall if not isinstance(wall, dict) else sum(wall.values())/4))

rows = list(csv.DictReader(open("tier3_engine_output_80_v2.csv", encoding="utf-8-sig")))
out = {}
for mode in ("C", "E1", "E2"):
    res = [run(r, mode) for r in rows]
    out[mode] = res
    y = [float(r["PET"]) for r in rows]; ym = [float(r["Tmrt"]) for r in rows]
    sun = [str(r["볕"]).strip() == "1" for r in rows]
    def st(idx):
        e = [res[i][1] - y[i] for i in idx]; et = [res[i][0] - ym[i] for i in idx]
        return f"PET MAE {sum(map(abs,e))/len(e):.2f} bias {sum(e)/len(e):+.2f} | Tmrt bias {sum(et)/len(et):+.2f}"
    allidx = range(len(rows)); sidx = [i for i in allidx if sun[i]]; hidx = [i for i in allidx if not sun[i]]
    hot = sum(1 for i in allidx if y[i] >= 41); hit = sum(1 for i in allidx if y[i] >= 41 and res[i][1] >= 41)
    print(f"[{mode}] 전체 {st(allidx)} | 양지 {st(sidx)} | 그늘 {st(hidx)} | 극심 {hit}/{hot}")
    wt = [w for _, _, w in res if w is not None]
    if wt: print(f"      벽온도 평균 {sum(wt)/len(wt):.1f}°C (n={len(wt)})")
# CSV 저장
fld = list(rows[0].keys())
with open("tier3_engine_output_80_v3.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fld + ["run_E1_Tmrt", "run_E1_PET", "run_E2_Tmrt", "run_E2_PET"])
    w.writeheader()
    for i, r in enumerate(rows):
        nr = dict(r); nr.update({"run_E1_Tmrt": round(out["E1"][i][0], 2), "run_E1_PET": round(out["E1"][i][1], 2),
                                 "run_E2_Tmrt": round(out["E2"][i][0], 2), "run_E2_PET": round(out["E2"][i][1], 2)})
        w.writerow(nr)
