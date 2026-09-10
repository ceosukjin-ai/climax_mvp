"""run_A(앱 입력 기상 + 앱 시계인자)를 현재 엔진 / 8월 배포 엔진 설정으로 각각 PET·UTCI 산출 → 앱 표시값과 비교."""
import csv, sys, dataclasses, numpy as np
from datetime import datetime
sys.path.insert(0, "backend")
from vpti_core import DEFAULT_CONFIG
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet
from run_tier3 import views, MATS

rows = list(csv.DictReader(open("data/tier3_engine_output_80_v4.csv", encoding="utf-8-sig")))
app = np.array([float(r["실측당시_앱pVPTI"]) for r in rows]); meas = np.array([float(r["PET"]) for r in rows])

def cfg_aug():
    mrt = dataclasses.replace(DEFAULT_CONFIG.mrt, dni_turbidity=1.0, hc_a=6.0, shade_ground_retention=0.0)
    return dataclasses.replace(DEFAULT_CONFIG, mrt=mrt)

def run(cfg, use_shade=True):
    pet = []; utci = []; tm = []
    for r in rows:
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
        shade = (1.0 if str(r["볕"]).strip() == "1" else 0.0) if use_shade else 1.0
        wc = WeatherContext(temperature_c=float(r["앱_입력_기온"]), humidity_pct=float(r["앱_입력_습도"]),
                            wind_speed_ms=float(r["앱_입력_풍속"]), wind_direction_deg=0.0)
        res = compute_vpti_thermal(views_5=views(float(r["앱_SVF"]), float(r["앱_GVI"])), materials=MATS, weather=wc,
                                   road_axis_deg=0.0, lat=float(r["위도"]), lon=float(r["경도"]), when=when,
                                   direct_shade=shade, config=cfg)
        tm.append(float(res.mrt.tmrt)); utci.append(float(res.vpti))
        pet.append(float(compute_pet(tdb=wc.temperature_c, tr=float(res.mrt.tmrt), v=res.pedestrian_wind_ms,
                                     rh=wc.humidity_pct, season=res.season, config=cfg.comfort).value))
    return np.array(pet), np.array(utci), np.array(tm)

def st(p, ref, name):
    e = p - ref; print(f"{name:44s} 평균 {p.mean():5.1f}  vs기준 MAE {np.mean(abs(e)):.2f} bias {e.mean():+.2f}")

print(f"앱 표시값 평균 {app.mean():.1f} / 실측 PET 평균 {meas.mean():.1f}\n")
for label, cfg in (("현재 엔진(9/10)", DEFAULT_CONFIG), ("8월 배포 설정(dni1.0·hc_a6·열관성0)", cfg_aug())):
    for sh, sname in ((True, "볕/그늘 반영"), (False, "그늘 무시(항상 볕)")):
        pet, utci, tm = run(cfg, sh)
        print(f"[{label} · {sname}]  Tmrt 평균 {tm.mean():.1f}")
        st(pet, app, "  PET  vs 앱표시값"); st(utci, app, "  UTCI vs 앱표시값"); st(pet, meas, "  PET  vs 실측")
    print()
