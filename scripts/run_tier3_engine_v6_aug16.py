#!/usr/bin/env python3
"""논문 검증용 최종 소급 — 8월 배포 엔진(커밋 9d00fc4, 2026-08-16 상태) 전체로 실측 80점 재실행 (v6).

엔진: scripts/engine_aug16/vpti_core (교정 전: dni_turbidity 없음·hc_a 6·열관성 없음) + 같은 시점의
      geo.sun_blocked_outdoor (앱이 8월에 쓰던 볕/그늘 자체 판정, V-World 건물 폴리곤).
운량: data/tier3_cloud_80.csv (8월 앱과 동일 방식, cloud_fraction 0~1).

run_A 격자기상 + 앱 시계인자,  볕/그늘 = 엔진 판정
run_B 격자기상 + tier3 시계,    볕/그늘 = 엔진 판정
run_C 실측기상 + tier3 시계,    볕/그늘 = 엔진 판정
run_D 실측기상 + 파노 시계,     볕/그늘 = 엔진 판정
run_E 실측기상 + tier3 시계,    볕/그늘 = 실측 '볕' 열 (진단용 상한)

실행(서버, 컨테이너 안에서 — V-World 키·네트워크 필요):
  cd ~/climax_mvp && docker compose --env-file infra/ncp/.env.prod -f infra/ncp/docker-compose.prod.yml \
    run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/run_tier3_engine_v6_aug16.py
출력: data/tier3_engine_output_80_v6.csv, data/tier3_engine_output_80_v6_stats.txt
"""
import asyncio, csv, importlib.util, math, os, sys
from datetime import datetime

ROOT = os.environ.get("CLIMAX_REPO", "/repo")
AUG = os.path.join(ROOT, "scripts", "engine_aug16")
sys.path.insert(0, AUG)                      # 8/16 vpti_core 가 /app 의 것보다 먼저
sys.path.insert(1, "/app")                   # app.config (V-World 키) 는 현재 것 사용

from vpti_core import DEFAULT_CONFIG          # noqa: E402  (8/16 버전)
from vpti_core.vsi import ViewSegmentation    # noqa: E402
from vpti_core.smti import MaterialFraction   # noqa: E402
from vpti_core.vpti import WeatherContext, compute_vpti_thermal  # noqa: E402
from vpti_core.comfort import compute_pet     # noqa: E402
from vpti_core.solar import estimate_solar    # noqa: E402

assert "engine_aug16" in os.path.dirname(compute_vpti_thermal.__code__.co_filename), "8/16 엔진이 아님"
_spec = importlib.util.spec_from_file_location("geo_aug16", os.path.join(AUG, "geo_aug16.py"))
geo = importlib.util.module_from_spec(_spec); sys.modules["geo_aug16"] = geo; _spec.loader.exec_module(geo)

IN = os.path.join(ROOT, "data", "tier3_engine_output_80_v5.csv")
CLOUD = os.path.join(ROOT, "data", "tier3_cloud_80.csv")
OUT = os.path.join(ROOT, "data", "tier3_engine_output_80_v6.csv")
STATS = os.path.join(ROOT, "data", "tier3_engine_output_80_v6_stats.txt")
MATS = [MaterialFraction(material="asphalt", fraction=0.7), MaterialFraction(material="concrete", fraction=0.3)]
BASE_COLS = ["측정ID", "시각", "지점명", "권역", "위도", "경도", "Ta", "RH", "v", "Tmrt", "PET", "Ts", "볕", "태양고도",
             "SVF", "TVF", "BVF", "앱_SVF", "앱_GVI", "앱_BVI", "앱_유효", "앱_입력_기온", "앱_입력_풍속", "앱_입력_습도",
             "실측당시_앱pVPTI", "엔진예측_PET", "엔진예측_Tmrt", "tier3_svf", "tier3_gvi", "tier3_bvi", "ndvi30", "lst30"]


def views(svf, gvi):
    g = max(0.0, min(1.0, gvi)); b = max(0.0, min(1.0 - g, 1.0 - svf)); sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)), vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=g, building_ratio=b) for d in ("front", "back", "left", "right")]
    return vs


def run(lat, lon, when, svf, gvi, ta, rh, v, shade, cf):
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    r = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc, road_axis_deg=0.0,
                             lat=lat, lon=lon, when=when, cloud_fraction=cf, direct_shade=shade)
    tm = float(r.mrt.tmrt)
    pet = float(compute_pet(tdb=ta, tr=tm, v=r.pedestrian_wind_ms, rh=rh, season=r.season, config=DEFAULT_CONFIG.comfort).value)
    return round(tm, 2), round(pet, 2)


async def engine_shade(lat, lon, when, cf):
    """8월 앱 방식: 태양 위치(운량 포함) → 건물 폴리곤 태양방향 차폐 → 그늘이면 0.0"""
    sun = estimate_solar(lat, lon, when, cloud_fraction=cf)
    if not sun.is_daytime:
        return 1.0, "야간"
    try:
        blocked, note = await asyncio.wait_for(geo.sun_blocked_outdoor(lat, lon, sun.solar_azimuth_deg, sun.solar_elevation_deg), timeout=15.0)
    except Exception as e:  # noqa: BLE001
        return 1.0, f"판정실패({type(e).__name__})"
    return (0.0 if blocked else 1.0), (note or "볕")


async def main():
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    cloud = {r["측정ID"]: r for r in csv.DictReader(open(CLOUD, encoding="utf-8-sig"))}
    out = []
    for i, r in enumerate(rows, 1):
        lat, lon = float(r["위도"]), float(r["경도"]); when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
        cf = float(cloud[r["측정ID"]]["cloud"])
        ta_m, rh_m, v_m = float(r["Ta"]), float(r["RH"]), float(r["v"])
        ta_a, rh_a, v_a = float(r["앱_입력_기온"]), float(r["앱_입력_습도"]), float(r["앱_입력_풍속"])
        sh_eng, note = await engine_shade(lat, lon, when, cf)
        sh_meas = 1.0 if str(r["볕"]).strip() == "1" else 0.0
        A = run(lat, lon, when, float(r["앱_SVF"]), float(r["앱_GVI"]), ta_a, rh_a, v_a, sh_eng, cf)
        B = run(lat, lon, when, float(r["tier3_svf"]), float(r["tier3_gvi"]), ta_a, rh_a, v_a, sh_eng, cf)
        C = run(lat, lon, when, float(r["tier3_svf"]), float(r["tier3_gvi"]), ta_m, rh_m, v_m, sh_eng, cf)
        D = run(lat, lon, when, float(r["SVF"]), float(r["TVF"]), ta_m, rh_m, v_m, sh_eng, cf)
        E = run(lat, lon, when, float(r["tier3_svf"]), float(r["tier3_gvi"]), ta_m, rh_m, v_m, sh_meas, cf)
        nr = {k: r[k] for k in BASE_COLS}
        nr.update({"cloud": cf, "cloud_src": cloud[r["측정ID"]]["cloud_src"],
                   "run_A_Tmrt": A[0], "run_A_PET": A[1], "run_B_Tmrt": B[0], "run_B_PET": B[1],
                   "run_C_Tmrt": C[0], "run_C_PET": C[1], "run_D_Tmrt": D[0], "run_D_PET": D[1],
                   "run_E_Tmrt": E[0], "run_E_PET": E[1],
                   "엔진볕_run_A": int(sh_eng), "엔진볕_run_B": int(sh_eng), "엔진볕_run_C": int(sh_eng),
                   "엔진볕_run_D": int(sh_eng), "볕_run_E": int(sh_meas), "엔진판정_메모": note})
        out.append(nr)
        print(f"  {i}/80 {r['측정ID']} 엔진볕={int(sh_eng)} 실측볕={int(sh_meas)} ({note})  A {A[1]} C {C[1]} E {E[1]}", flush=True)
    fields = list(out[0].keys())
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(out)
    print(f"저장: {OUT}")

    # ── 통계 ──
    lines = []
    def P(s=""): print(s); lines.append(s)
    def stats(rs, k, ref="PET"):
        d = [(float(x[ref]), float(x[k])) for x in rs]; n = len(d)
        e = [b - a for a, b in d]; mae = sum(map(abs, e)) / n; bias = sum(e) / n
        mo = sum(a for a, _ in d) / n; mr = sum(b for _, b in d) / n
        cov = sum((a - mo) * (b - mr) for a, b in d)
        so = math.sqrt(sum((a - mo) ** 2 for a, _ in d)); sr = math.sqrt(sum((b - mr) ** 2 for _, b in d))
        rr = cov / (so * sr) if so > 0 and sr > 0 else float("nan")
        hot = [x for x in rs if float(x["PET"]) >= 41]; hit = sum(1 for x in hot if float(x[k]) >= 41)
        return mae, bias, rr, hit, len(hot)
    P("=== v6 · 8월 배포 엔진(9d00fc4) · 운량 반영 · 볕/그늘 엔진 판정 (E만 실측 볕) ===")
    for gname, g in (("전체(80)", out), ("앱유효(55)", [x for x in out if x["앱_유효"] == "유효"]), ("못쓴(25)", [x for x in out if x["앱_유효"] != "유효"])):
        P(f"\n[{gname}]")
        for k in ("run_A_PET", "run_B_PET", "run_C_PET", "run_D_PET", "run_E_PET"):
            mae, bias, rr, hit, nh = stats(g, k)
            P(f"  {k:10s} MAE {mae:5.2f}  bias {bias:+6.2f}  r {rr:+.3f}  극심 {hit}/{nh}")
    mae, bias, rr, _, _ = stats(out, "run_A_PET", "실측당시_앱pVPTI")
    P(f"\n=== run_A vs 앱 표시값(실측당시_앱pVPTI): MAE {mae:.2f}  bias {bias:+.2f}  r {rr:+.3f} ===")
    tp = sum(1 for x in out if x["엔진볕_run_A"] == 1 and x["볕_run_E"] == 1); fn = sum(1 for x in out if x["엔진볕_run_A"] == 0 and x["볕_run_E"] == 1)
    fp = sum(1 for x in out if x["엔진볕_run_A"] == 1 and x["볕_run_E"] == 0); tn = sum(1 for x in out if x["엔진볕_run_A"] == 0 and x["볕_run_E"] == 0)
    P("\n=== 엔진 볕/그늘 판정 vs 실측 볕 (혼동행렬) ===")
    P(f"                 실측 볕   실측 그늘")
    P(f"  엔진 볕        {tp:4d}      {fp:4d}")
    P(f"  엔진 그늘      {fn:4d}      {tn:4d}")
    P(f"  정확도 {(tp+tn)/80:.0%}  · 실측 그늘을 그늘로 잡은 비율 {tn/max(1,tn+fp):.0%}  · 실측 볕을 볕으로 {tp/max(1,tp+fn):.0%}")
    open(STATS, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"저장: {STATS}")


if __name__ == "__main__":
    asyncio.run(main())
