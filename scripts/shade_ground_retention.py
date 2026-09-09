import csv, math, sys, numpy as np
from datetime import datetime
sys.path.insert(0, ".")
from vpti_core import DEFAULT_CONFIG, estimate_solar
from vpti_core.mrt import estimate_ground_temp, sky_emissivity, ground_properties_from_materials
from vpti_core.materials import get_properties
from run_tier3 import MATS
alb, emi = ground_properties_from_materials(MATS, get_properties)
rows = list(csv.DictReader(open("tier3_engine_output_80_v2.csv", encoding="utf-8-sig")))
ret=[]; sun_err=[]
for r in rows:
    lat, lon = float(r["위도"]), float(r["경도"]); when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
    ta, rh, v, svf, gvi, ts = (float(r[k]) for k in ("Ta","RH","v","tier3_svf","tier3_gvi","Ts"))
    sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
    eps = sky_emissivity(ta, rh, sol.cloud_fraction, DEFAULT_CONFIG.mrt)
    t1 = estimate_ground_temp(ta, sol, alb, emi, svf, gvi, v, eps, DEFAULT_CONFIG.mrt, 1.0)
    t0 = estimate_ground_temp(ta, sol, alb, emi, svf, gvi, v, eps, DEFAULT_CONFIG.mrt, 0.0)
    if str(r["볕"]).strip()=="1": sun_err.append(t1-ts)
    else: ret.append(((ts-t0)/(t1-t0), ts, t0, t1))
ret=np.array(ret)
print("양지: 엔진 Ts − 실측 Ts bias %.1f (n=%d)"%(np.mean(sun_err),len(sun_err)))
print("그늘 n=%d 실측Ts %.1f 엔진 그늘Ts %.1f 엔진 양지Ts %.1f"%(len(ret),ret[:,1].mean(),ret[:,2].mean(),ret[:,3].mean()))
print("보존율 (Ts−T0)/(T1−T0): 중앙 %.2f 평균 %.2f q25 %.2f q75 %.2f"%(np.median(ret[:,0]),ret[:,0].mean(),*np.percentile(ret[:,0],[25,75])))
