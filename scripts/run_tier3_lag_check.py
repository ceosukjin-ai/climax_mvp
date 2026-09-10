#!/usr/bin/env python3
"""뇌 v1 ①b-(c): 지면 열관성(시간지연·야간방출) 후보 계수를 실측 80점에 대보기 (2026-09-10).

ASOS(잔디 개활지)로 맞춘 구조 항이 도시 아스팔트 80점(정오, 열화상 Ts·PET)을 해치지 않는지 확인.
run_C(실측기상 + tier3 시계 + 실측 볕/그늘 + 현장풍속 + 운량) 를 현재 계수 vs 후보 계수로 돌려
Ts(열화상)·Tmrt·PET 의 MAE/bias, 극심(PET≥41) 재현을 비교. 엔진 기본값은 그대로(dry-run).

실행(서버 컨테이너): ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/run_tier3_lag_check.py
"""
import csv, math, os, sys
from dataclasses import replace
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/app"); sys.path.insert(0, os.path.join(os.environ.get("CLIMAX_REPO", "/repo"), "backend"))
from vpti_core import DEFAULT_CONFIG
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet

ROOT = os.environ.get("CLIMAX_REPO", "/repo")
IN = os.path.join(ROOT, "data", "tier3_engine_output_80_v4.csv")
CLOUD = os.path.join(ROOT, "data", "tier3_cloud_80.csv")
KST = timezone(timedelta(hours=9))
MATS = [MaterialFraction(material="asphalt", fraction=0.7), MaterialFraction(material="concrete", fraction=0.3)]
CANDS = {
    "현재": {},
    "후보A tau2 q60 f0.5": dict(ground_lag_tau_h=2.0, ground_release_wm2=60.0, ground_storage_fraction=0.5),
    "후보B tau2 q40 f0.4": dict(ground_lag_tau_h=2.0, ground_release_wm2=40.0, ground_storage_fraction=0.4),
    "후보C tau1.5 q60 f0.4": dict(ground_lag_tau_h=1.5, ground_release_wm2=60.0, ground_storage_fraction=0.4),
    "후보D tau2 q60 f0.25": dict(ground_lag_tau_h=2.0, ground_release_wm2=60.0),
    "후보E tau2 q40 f0.25": dict(ground_lag_tau_h=2.0, ground_release_wm2=40.0),
    "후보F tau2.5 q60 f0.25": dict(ground_lag_tau_h=2.5, ground_release_wm2=60.0),
    "tau2만": dict(ground_lag_tau_h=2.0),
    "q60만": dict(ground_release_wm2=60.0),
}


def views(svf, gvi):
    g = max(0.0, min(1.0, gvi)); b = max(0.0, min(1.0 - g, 1.0 - svf)); sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)), vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=g, building_ratio=b) for d in ("front", "back", "left", "right")]
    return vs


def run(cfg, lat, lon, when, svf, gvi, ta, rh, v, shade, cf):
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    r = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc, road_axis_deg=0.0,
                             lat=lat, lon=lon, when=when, cloud_fraction=cf, direct_shade=shade,
                             wind_is_pedestrian=True, config=cfg)
    tm = float(r.mrt.tmrt)
    pet = float(compute_pet(tdb=ta, tr=tm, v=r.pedestrian_wind_ms, rh=rh, season=r.season, config=cfg.comfort).value)
    return float(r.mrt.ground_temp_c), tm, pet


def stats(pairs):
    p = [(a, b) for a, b in pairs if a is not None]; n = len(p)
    e = [b - a for a, b in p]; mae = sum(map(abs, e)) / n; bias = sum(e) / n
    mo = sum(a for a, _ in p) / n; mr = sum(b for _, b in p) / n
    so = math.sqrt(sum((a - mo) ** 2 for a, _ in p)); sr = math.sqrt(sum((b - mr) ** 2 for _, b in p))
    r = sum((a - mo) * (b - mr) for a, b in p) / (so * sr) if so and sr else float("nan")
    return mae, bias, r


def main():
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    cloud = {r["측정ID"]: float(r["cloud"]) for r in csv.DictReader(open(CLOUD, encoding="utf-8-sig"))}
    res = {k: [] for k in CANDS}
    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"]); when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        shade = 1.0 if str(r["볕"]).strip() == "1" else 0.0
        ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"]); cf = cloud.get(r["측정ID"], 0.0)
        ts_obs = float(r["Ts"]) if r.get("Ts") not in (None, "") else None
        for k, ov in CANDS.items():
            cfg = replace(DEFAULT_CONFIG, mrt=replace(DEFAULT_CONFIG.mrt, **ov)) if ov else DEFAULT_CONFIG
            ts, tm, pet = run(cfg, lat, lon, when, float(r["tier3_svf"]), float(r["tier3_gvi"]), ta, rh, v, shade, cf)
            res[k].append({"ts_obs": ts_obs, "ts": ts, "tm_obs": float(r["Tmrt"]), "tm": tm, "pet_obs": float(r["PET"]), "pet": pet, "shade": shade})
    print("=== run_C(실측기상+tier3시계+실측볕+현장풍속+운량) — 80점, 현재 vs 후보 ===")
    print(f"{'계수':24s} {'Ts MAE/bias/r':>22s} {'Tmrt MAE/bias/r':>22s} {'PET MAE/bias/r':>22s}  극심   Ts그늘bias  Ts양지bias")
    for k, L in res.items():
        tsm = stats([(x["ts_obs"], x["ts"]) for x in L]); tmm = stats([(x["tm_obs"], x["tm"]) for x in L]); pm = stats([(x["pet_obs"], x["pet"]) for x in L])
        hot = [x for x in L if x["pet_obs"] >= 41]; hit = sum(1 for x in hot if x["pet"] >= 41)
        sh = stats([(x["ts_obs"], x["ts"]) for x in L if x["shade"] == 0.0])[1]; su = stats([(x["ts_obs"], x["ts"]) for x in L if x["shade"] == 1.0])[1]
        print(f"{k:24s} {tsm[0]:6.2f} {tsm[1]:+6.2f} {tsm[2]:+.2f}   {tmm[0]:6.2f} {tmm[1]:+6.2f} {tmm[2]:+.2f}   {pm[0]:6.2f} {pm[1]:+6.2f} {pm[2]:+.2f}   {hit}/{len(hot)}   {sh:+6.2f}    {su:+6.2f}")
    print("\n게이트: 후보가 현재 대비 PET MAE 악화 ≤0.3 & 극심 놓침 증가 없음 & Ts bias |≤3| 이면 승격 가능.")


if __name__ == "__main__":
    main()
