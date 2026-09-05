#!/usr/bin/env python3
# f_stor / h_c 실측 Ts 회귀 교정 (서버, vpti_core 물리 재사용). 2026-09-05
import json,sys,math
sys.path.insert(0,"backend")
from datetime import datetime,timezone,timedelta
from vpti_core.solar import estimate_solar
from vpti_core.mrt import estimate_ground_temp, sky_emissivity
from vpti_core.config import DEFAULT_CONFIG
from dataclasses import replace
KST=timezone(timedelta(hours=9))
ROWS=json.load(open("/home/ubuntu/climax_mvp/cal_rows.json"))
ALB, EMIS = 0.08, 0.94   # 보행로 노면 아스팔트/콘크리트 대표
def eng_ts(r, cfg):
    t=datetime.strptime(r["t"],"%Y-%m-%d %H:%M").replace(tzinfo=KST)
    cf=0.0 if r["expose"]=="sun" else 0.5
    sol=estimate_solar(r["lat"],r["lon"],t,cloud_fraction=cf)
    eps_sky=sky_emissivity(r["ta"],r["rh"],cf=cf)
    ds=1.0 if r["expose"]=="sun" else 0.0
    return estimate_ground_temp(r["ta"],sol,ALB,EMIS,r["svf"],r["gvi"],r["wind"],eps_sky,config=cfg,direct_shade=ds)
def score_cfg(cfg):
    res=[e-r["ts_obs"] for r in ROWS for e in [safe(r,cfg)] if e is not None]
    n=len(res); b=sum(res)/n; rm=math.sqrt(sum(x*x for x in res)/n)
    return rm,b,n
def safe(r,cfg):
    try: return eng_ts(r,cfg)
    except Exception: return None
base=DEFAULT_CONFIG.mrt
print("현재 계수: f_stor={:.2f} hc_a={:.1f} hc_b={:.1f}".format(base.ground_storage_fraction,base.hc_a,base.hc_b))
rm,b,n=score_cfg(base); print("현재: RMSE={:.2f} bias={:+.2f} n={}".format(rm,b,n))
best=None
for fs in [x/100 for x in range(20,76,5)]:
  for ha in [4,6,8,10,12,15,18,22]:
    for hb in [4,6,8]:
      cfg=replace(base, ground_storage_fraction=fs, hc_a=ha, hc_b=hb)
      rm,b,n=score_cfg(cfg); s=rm+abs(b)
      if best is None or s<best[0]: best=(s,fs,ha,hb,rm,b)
print("최적(RMSE+|bias|): f_stor={:.2f} hc_a={} hc_b={} → RMSE={:.2f} bias={:+.2f}".format(best[1],best[2],best[3],best[4],best[5]))
