#!/usr/bin/env python3
"""3계단 시계인자로 엔진 PET 소급 실행 (2026-09-09).
tier3_engine_input_80.csv → 행마다 4개 조합(run_A/B/C/D) Tmrt·PET → output CSV + 통계표.
엔진 계수·내부 로직은 배포 버전 그대로(compute_vpti_thermal → compute_mrt → PET).
"""
import csv, sys, math
from datetime import datetime
sys.path.insert(0, "backend")
from vpti_core import DEFAULT_CONFIG
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet

IN = "data/tier3_engine_output_80_v4.csv"
OUT = "data/tier3_engine_output_80_v7.csv"

# 지면 재질: 파일에 재질분율이 없어 도시 표준 고정(모든 run 공통 → 상대비교 불변).
MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]


def views(svf, gvi):
    """스칼라 SVF/GVI → 5-view 재구성(routes._geo_vpti_compute 방식).
    up.sky=SVF, 수평.sky=SVF/2 로 reconstruct_svf 가 원래 SVF 복원, 수평.veg=GVI."""
    g = max(0.0, min(1.0, gvi))
    b = max(0.0, min(1.0 - g, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=g, building_ratio=b)
           for d in ("front", "back", "left", "right")]
    return vs


def run(lat, lon, when, svf, gvi, ta, rh, v, shade, ped=False, cf=None):
    """한 조합 → (Tmrt, PET). 배포 엔진 그대로."""
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    r = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc,
                             road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                             direct_shade=shade, wind_is_pedestrian=ped, cloud_fraction=cf)
    tmrt = float(r.mrt.tmrt)
    pet = compute_pet(tdb=ta, tr=tmrt, v=r.pedestrian_wind_ms, rh=rh,
                      season=r.season, config=DEFAULT_CONFIG.comfort)
    return round(tmrt, 2), round(float(pet.value), 2)


def main():
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    CLOUD = {r["측정ID"]: r for r in csv.DictReader(open("data/tier3_cloud_80.csv", encoding="utf-8-sig"))}
    fld = list(rows[0].keys())
    extra = ["cloud", "cloud_src", "run_A_Tmrt", "run_A_PET", "run_B_Tmrt", "run_B_PET",
             "run_C_Tmrt", "run_C_PET", "run_D_Tmrt", "run_D_PET"]
    out_rows = []
    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
        shade = 1.0 if str(r["볕"]).strip() == "1" else 0.0
        ta_m, rh_m, v_m = float(r["Ta"]), float(r["RH"]), float(r["v"])          # 실측 기상
        ta_a, rh_a, v_a = float(r["앱_입력_기온"]), float(r["앱_입력_습도"]), float(r["앱_입력_풍속"])  # 앱 입력
        svf_app, gvi_app = float(r["앱_SVF"]), float(r["앱_GVI"])
        svf_t3, gvi_t3 = float(r["tier3_svf"]), float(r["tier3_gvi"])
        svf_pano, gvi_pano = float(r["SVF"]), float(r["TVF"])                     # 파노(TVF=tree view)
        cf = float(CLOUD[r["측정ID"]]["cloud"]); csrc = CLOUD[r["측정ID"]]["cloud_src"]
        a_t, a_p = run(lat, lon, when, svf_app, gvi_app, ta_a, rh_a, v_a, shade, False, cf)  # A: 앱기상+앱시계
        b_t, b_p = run(lat, lon, when, svf_t3, gvi_t3, ta_a, rh_a, v_a, shade, False, cf)    # B: 앱기상+tier3
        c_t, c_p = run(lat, lon, when, svf_t3, gvi_t3, ta_m, rh_m, v_m, shade, True, cf)    # C: 실측기상+tier3
        d_t, d_p = run(lat, lon, when, svf_pano, gvi_pano, ta_m, rh_m, v_m, shade, True, cf)  # D: 실측기상+파노
        nr = dict(r)
        nr.update({"cloud": cf, "cloud_src": csrc, "run_A_Tmrt": a_t, "run_A_PET": a_p, "run_B_Tmrt": b_t, "run_B_PET": b_p,
                   "run_C_Tmrt": c_t, "run_C_PET": c_p, "run_D_Tmrt": d_t, "run_D_PET": d_p})
        out_rows.append(nr)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fld + extra)
        w.writeheader(); w.writerows(out_rows)
    print(f"출력 완료: {OUT} ({len(out_rows)}행)")
    return out_rows


# ── 통계 ──────────────────────────────────────────
def stats(rows, run_key, ref_key="PET"):
    d = [(float(r[ref_key]), float(r[run_key])) for r in rows]
    n = len(d)
    if n == 0:
        return None
    err = [b - a for a, b in d]                        # run - 실측
    mae = sum(abs(e) for e in err) / n
    bias = sum(err) / n
    mo = sum(a for a, _ in d) / n; mr = sum(b for _, b in d) / n
    cov = sum((a - mo) * (b - mr) for a, b in d)
    so = math.sqrt(sum((a - mo) ** 2 for a, _ in d)); sr = math.sqrt(sum((b - mr) ** 2 for _, b in d))
    r = cov / (so * sr) if so > 0 and sr > 0 else float("nan")
    return {"n": n, "MAE": round(mae, 2), "bias": round(bias, 2), "r": round(r, 3)}


def extreme(rows, run_key, thr=41.0):
    hot = [r for r in rows if float(r["PET"]) >= thr]
    hit = sum(1 for r in rows if float(r["PET"]) >= thr and float(r[run_key]) >= thr)
    return f"{hit}/{len(hot)}" if hot else "-"


if __name__ == "__main__":
    rows = main()
    groups = {"전체(80)": rows,
              "앱유효(55)": [r for r in rows if r["앱_유효"] == "유효"],
              "나머지(25)": [r for r in rows if r["앱_유효"] != "유효"]}
    print("\n=== run별 실측 PET 대비 (MAE·bias·r) / 극심 PET>=41 민감도 ===")
    for gname, g in groups.items():
        print(f"\n[{gname}]")
        for rk in ("run_A_PET", "run_B_PET", "run_C_PET", "run_D_PET"):
            s = stats(g, rk); ex = extreme(g, rk)
            print(f"  {rk:11s} MAE {s['MAE']:5.2f}  bias {s['bias']:+6.2f}  r {s['r']:+.3f}  극심 {ex}")
    # run_A vs 앱 표시값(실측당시_앱pVPTI) & 엔진예측_PET
    print("\n=== run_A_PET vs 앱표시값/엔진예측 (재현·버전차) ===")
    for ref in ("실측당시_앱pVPTI", "엔진예측_PET"):
        s = stats(rows, "run_A_PET", ref_key=ref)
        print(f"  vs {ref}: MAE {s['MAE']:.2f}  bias {s['bias']:+.2f}  r {s['r']:+.3f}")
