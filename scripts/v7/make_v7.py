import pandas as pd, numpy as np, sys, os, csv
from datetime import datetime
D = pd.read_csv("/mnt/user-data/uploads/climax_mvp/data/tier3_engine_output_80_v6.csv")
V = pd.read_csv("aug80_sunshade_photo_verify.csv")
# 경계 2건: 흑구상승으로 판정 (17.2 K 중간값 기준)
V.loc[V.측정ID=="20260820_서제2동_13","사진볕"] = 1   # 18.7 K
V.loc[V.측정ID=="20260823_명장제7동_01","사진볕"] = 0  # 11.9 K
V["확신도"] = V.확신도.where(~V.측정ID.isin(["20260820_서제2동_13","20260823_명장제7동_01"]), "low→globe")
m = V.set_index("측정ID")
D["볕_v6"] = D["볕"]
D["볕"] = D.측정ID.map(m.사진볕).astype(int)
D["볕_src"] = "photo360-verified 2026-09-19 (direct beam at sensor; cloud-diffuse=0)"
D["볕_사진판정"] = D.측정ID.map(m.사진판정)
D["볕_확신도"] = D.측정ID.map(m.확신도)
print("volume:", D.볕.sum(), "sun /", (D.볕==0).sum(), "shade; changed:", (D.볕!=D.볕_v6).sum())

# ---- run_E 재계산 (8/16 엔진, 실측볕만 교체) ----
sys.path.insert(0, "eng/scripts/engine_aug16")
from vpti_core import DEFAULT_CONFIG
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet
MATS = [MaterialFraction(material="asphalt", fraction=0.7), MaterialFraction(material="concrete", fraction=0.3)]
def views(svf, gvi):
    g = max(0.0, min(1.0, gvi)); b = max(0.0, min(1.0 - g, 1.0 - svf)); sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)), vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=g, building_ratio=b) for d in ("front","back","left","right")]
    return vs
def run(lat, lon, when, svf, gvi, ta, rh, v, shade, cf):
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    r = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc, road_axis_deg=0.0,
                             lat=lat, lon=lon, when=when, cloud_fraction=cf, direct_shade=shade)
    tm = float(r.mrt.tmrt)
    pet = float(compute_pet(tdb=ta, tr=tm, v=r.pedestrian_wind_ms, rh=rh, season=r.season, config=DEFAULT_CONFIG.comfort).value)
    return round(tm, 2), round(pet, 2)
cloud = pd.read_csv("eng/data/tier3_cloud_80.csv").set_index("측정ID")
D["run_E_Tmrt_v6"], D["run_E_PET_v6"] = D.run_E_Tmrt, D.run_E_PET
chk = []
for i, r in D.iterrows():
    when = datetime.strptime(r.시각, "%Y-%m-%d %H:%M:%S"); cf = float(cloud.loc[r.측정ID, "cloud"])
    # 재현 검증: v6 라벨로
    tm0, pet0 = run(r.위도, r.경도, when, r.tier3_svf, r.tier3_gvi, r.Ta, r.RH, r.v, float(r.볕_v6), cf)
    chk.append(abs(pet0 - r.run_E_PET_v6))
    tm, pet = run(r.위도, r.경도, when, r.tier3_svf, r.tier3_gvi, r.Ta, r.RH, r.v, float(r.볕), cf)
    D.loc[i, "run_E_Tmrt"], D.loc[i, "run_E_PET"] = tm, pet
    D.loc[i, "볕_run_E"] = int(r.볕)
print("v6 run_E 재현 최대오차:", max(chk))
D["볕_run_E"] = D.볕_run_E.astype(int)
D.to_csv("tier3_engine_output_80_v7_photo.csv", index=False, encoding="utf-8-sig")
def st(k):
    e = D[k] - D.PET; hot = D.PET >= 41
    r = np.corrcoef(D.PET, D[k])[0,1]
    return f"MAE {e.abs().mean():.2f} bias {e.mean():+.2f} r {r:.2f} hot {int((D[k][hot]>=41).sum())}/{int(hot.sum())}"
print("run_E v6 :", st("run_E_PET_v6")); print("run_E v7 :", st("run_E_PET"))
for k in ["run_A_PET","run_B_PET","run_C_PET","run_D_PET"]: print(k, st(k))
# kappa 엔진판정 vs 라벨
def kappa(a, b):
    a, b = np.asarray(a), np.asarray(b); po = (a==b).mean()
    pe = (a==1).mean()*(b==1).mean() + (a==0).mean()*(b==0).mean(); return (po-pe)/(1-pe)
print("κ 엔진볕 vs v6:", round(kappa(D.엔진볕_run_A, D.볕_v6),3), " vs v7:", round(kappa(D.엔진볕_run_A, D.볕),3))
tp=((D.엔진볕_run_A==1)&(D.볕==1)).sum(); fp=((D.엔진볕_run_A==1)&(D.볕==0)).sum(); fn=((D.엔진볕_run_A==0)&(D.볕==1)).sum(); tn=((D.엔진볕_run_A==0)&(D.볕==0)).sum()
print("혼동(v7): tp",tp,"fp",fp,"fn",fn,"tn",tn)
print(D[D.볕!=D.볕_v6][["측정ID","볕_v6","볕","run_E_PET_v6","run_E_PET","PET"]])
