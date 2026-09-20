import sys, math, csv, importlib.util
from datetime import datetime, timedelta
sys.path.insert(0, "be/backend")
import pandas as pd, numpy as np
from vpti_core import DEFAULT_CONFIG
from vpti_core.solar import estimate_solar
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.mrt import estimate_wall_temp, estimate_wall_temp_transient, estimate_ground_temp, sky_emissivity
spec = importlib.util.spec_from_file_location("wm", "be/backend/app/services/wall_material.py"); wm = importlib.util.module_from_spec(spec); spec.loader.exec_module(wm)
MATS = [MaterialFraction(material="asphalt", fraction=0.7), MaterialFraction(material="concrete", fraction=0.3)]
GROUND_ALBEDO, GROUND_EMISSIVITY = 0.155, 0.94
FN = {"N": 180.0, "E": 270.0, "S": 0.0, "W": 90.0}
def views(svf, gvi):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf)); sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)), vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi, building_ratio=b) for d in ("front","back","left","right")]
    return vs
def run_engine(lat, lon, when, svf, gvi, ta, rh, v, shade, alb, emi, hc):
    sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
    epsk = sky_emissivity(ta, rh, sol.cloud_fraction, DEFAULT_CONFIG.mrt)
    wall = None
    if svf < 0.92 and sol.solar_elevation_deg > 0:
        ser = []
        for h in range(12, -1, -1):
            t = when - timedelta(hours=h); s = estimate_solar(lat, lon, t, config=DEFAULT_CONFIG.solar)
            ek = sky_emissivity(ta, rh, s.cloud_fraction, DEFAULT_CONFIG.mrt)
            tg = estimate_ground_temp(ta, s, GROUND_ALBEDO, GROUND_EMISSIVITY, svf, gvi, v, ek, DEFAULT_CONFIG.mrt, shade, None)
            ser.append((h*3600.0, ta, s.dni, s.dhi, s.solar_elevation_deg, s.solar_azimuth_deg, tg, GROUND_ALBEDO))
        tg_now = estimate_ground_temp(ta, sol, GROUND_ALBEDO, GROUND_EMISSIVITY, svf, gvi, v, epsk, DEFAULT_CONFIG.mrt, shade, None)
        wall = {}
        for d, fn in FN.items():
            cosf = max(0.0, math.cos(math.radians(sol.solar_azimuth_deg - fn)))
            wt = estimate_wall_temp_transient(ser, alb, emi, cosf, v, epsk, hc, DEFAULT_CONFIG.mrt, facade_normal_deg=fn)
            if wt is None:
                wt = estimate_wall_temp(ta, sol, alb, emi, cosf, v, epsk, DEFAULT_CONFIG.mrt, ground_temp_c=tg_now, ground_albedo=GROUND_ALBEDO)
            wall[d] = wt
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    return compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc, road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                                direct_shade=shade, wall_temp_c=wall, wall_albedo=alb, wind_is_pedestrian=True)
B = pd.read_csv("/mnt/user-data/uploads/climax_mvp/data/tier3_80_body_260913.csv")
V = pd.read_csv("tier3_engine_output_80_v7_photo.csv").set_index("측정ID")
p = wm.props_for("concrete"); alb, emi, hc = p["albedo"], p["emissivity"], p.get("hc", 100000.0)
rows = []
for _, r in B.iterrows():
    when = datetime.strptime(r.시각, "%Y-%m-%d %H:%M:%S")
    def delta(sh):
        res = run_engine(r.위도, r.경도, when, r.tier3_svf, r.tier3_gvi, r.Ta, r.RH, r.v, sh, alb, emi, hc)
        return float(res.mrt.tmrt_globe) - float(res.mrt.tmrt)
    d6 = delta(float(r.볕)); d7 = delta(float(V.loc[r.측정ID, "볕"]))
    rows.append(dict(측정ID=r.측정ID, 볕_v6=int(r.볕), 볕_v7=int(V.loc[r.측정ID,"볕"]), 정의차_기록=float(r["정의차_흑구−전신"]), 정의차_재현=round(d6,2), 정의차_v7=round(d7,2), 태양고도=r.태양고도))
T = pd.DataFrame(rows)
print("재현 오차(콘크리트 기본 벽재질): MAE", (T.정의차_재현-T.정의차_기록).abs().mean().round(3), "max", (T.정의차_재현-T.정의차_기록).abs().max().round(3))
print(T[T.볕_v6!=T.볕_v7])
T.to_csv("delta_v7.csv", index=False, encoding="utf-8-sig")
print("v7: overall", T.정의차_v7.mean().round(2), "sun", T.정의차_v7[T.볕_v7==1].mean().round(2), "nobeam", T.정의차_v7[T.볕_v7==0].mean().round(2))
