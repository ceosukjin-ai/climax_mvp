#!/usr/bin/env python3
"""80점 기준을 **전신 Tmrt 기준**으로 다시 만든다 (2026-09-13).

왜:
  실측 Tmrt·PET 는 흑구(Ø0.05m, ε0.95, ISO 7726)에서 온다. 확인: 흑구Tg·Ta·v 로 ISO 역산하면
  시트의 Tmrt 를 MAE 0.37℃ 로 재현한다.
  흑구는 구체라 어느 방향에서도 투영면적비 0.25 로 빔을 받고 단파를 0.95 흡수한다.
  서 있는 사람은 태양고도에 따라 fp 0.08~0.3(Fanger), 흡수 a_k≈0.7. 해가 높을수록 격차가 커진다.
  PET 는 **서 있는 사람**에 대해 정의되므로 올바른 입력은 전신 Tmrt 다.

  그동안 엔진은 전신 Tmrt 를 흑구 실측에 맞추도록 교정돼 왔다. 즉 물리적으로 틀린 벽온도
  (벽=노면, 열화상 실측 대비 +11℃)로 Tmrt 를 부풀려 뜨거운 기준에 맞추고 있었다.
  잔차 AI 도 그 기준으로 학습됐다. 벽 물리만 고치면 AI 가 도로 끌어올린다.

방법:
  엔진은 같은 복사환경에서 전신 Tmrt 와 흑구 Tmrt 를 **둘 다** 낸다(mrt.py, 2026-09-13).
  그 차이 Δ = Tmrt_흑구(엔진) − Tmrt_전신(엔진) 를 실측에서 빼 전신 기준 실측을 만든다.

      Tmrt_전신(실측) = Tmrt_흑구(실측) − Δ
      PET_전신(실측)  = compute_pet(Ta, Tmrt_전신(실측), v, RH)

  Δ 는 모델에서 오지만, 그 크기는 독립적으로 확인됐다 — 2026-09-05 3단분해가 기록한
  "MRT 잔여 +9.6℃ = 흑구 vs 전신 정의차" 와 이 스크립트의 Δ(+8.7~10.4℃)가 일치한다.

  엔진 출력(run_C_*)도 배포 예정 설정(벽 스테이지 ON + 벽 단파반사 ON)으로 다시 채운다.

출력: 새 CSV. 이어서 train_pet_residual.py 에 넣어 잔차 AI 를 재학습한다.

  docker exec -i climax-api python3 /tmp/rebase_reference_body.py <in.csv> <out.csv>
"""
from __future__ import annotations
import csv, math, sys
from datetime import datetime, timedelta
sys.path.insert(0, "/app"); sys.path.insert(0, "/repo/backend")

from vpti_core import DEFAULT_CONFIG
from vpti_core.solar import estimate_solar
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet
from vpti_core.mrt import (estimate_wall_temp, estimate_wall_temp_transient,
                           estimate_ground_temp, sky_emissivity)
from app.services.geo import dominant_wall_material
from app.services.wall_material import props_for

MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]
GROUND_ALBEDO, GROUND_EMISSIVITY = 0.155, 0.94
FN = {"N": 180.0, "E": 270.0, "S": 0.0, "W": 90.0}


def views(svf, gvi):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi, building_ratio=b)
           for d in ("front", "back", "left", "right")]
    return vs


def run_engine(lat, lon, when, svf, gvi, ta, rh, v, shade, alb, emi, hc):
    """배포 예정 설정: 벽 스테이지 ON(과도·노면 봄) + 벽 단파반사 ON."""
    sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
    epsk = sky_emissivity(ta, rh, sol.cloud_fraction, DEFAULT_CONFIG.mrt)
    wall = None
    if svf < 0.92 and sol.solar_elevation_deg > 0:
        ser = []
        for h in range(12, -1, -1):
            t = when - timedelta(hours=h)
            s = estimate_solar(lat, lon, t, config=DEFAULT_CONFIG.solar)
            ek = sky_emissivity(ta, rh, s.cloud_fraction, DEFAULT_CONFIG.mrt)
            tg = estimate_ground_temp(ta, s, GROUND_ALBEDO, GROUND_EMISSIVITY, svf, gvi, v, ek,
                                      DEFAULT_CONFIG.mrt, shade, None)
            ser.append((h * 3600.0, ta, s.dni, s.dhi,
                        s.solar_elevation_deg, s.solar_azimuth_deg, tg, GROUND_ALBEDO))
        tg_now = estimate_ground_temp(ta, sol, GROUND_ALBEDO, GROUND_EMISSIVITY, svf, gvi, v, epsk,
                                      DEFAULT_CONFIG.mrt, shade, None)
        wall = {}
        for d, fn in FN.items():
            cosf = max(0.0, math.cos(math.radians(sol.solar_azimuth_deg - fn)))
            wt = estimate_wall_temp_transient(ser, alb, emi, cosf, v, epsk, hc,
                                              DEFAULT_CONFIG.mrt, facade_normal_deg=fn)
            if wt is None:
                wt = estimate_wall_temp(ta, sol, alb, emi, cosf, v, epsk, DEFAULT_CONFIG.mrt,
                                        ground_temp_c=tg_now, ground_albedo=GROUND_ALBEDO)
            wall[d] = wt
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    r = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc,
                             road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                             direct_shade=shade, wall_temp_c=wall, wall_albedo=alb,
                             wind_is_pedestrian=True)
    return r


def pet_of(ta, tr, v_ped, rh, season):
    return float(compute_pet(tdb=ta, tr=tr, v=v_ped, rh=rh, season=season,
                             config=DEFAULT_CONFIG.comfort).value)


async def main():
    import asyncio  # noqa: F401
    src, dst = sys.argv[1], sys.argv[2]
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    print(f"입력 {len(rows)}행  {src}")
    cache = {}
    out, deltas = [], []
    for r in rows:
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
        shade = 1.0 if str(r["볕"]).strip() == "1" else 0.0
        ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"])
        svf, gvi = float(r["tier3_svf"]), float(r["tier3_gvi"])
        k = (round(lat, 5), round(lon, 5))
        if k not in cache:
            cache[k] = await dominant_wall_material(lat, lon)
        wm = cache[k]
        alb, emi = wm["albedo"], wm["emissivity"]
        hc = wm.get("hc") or props_for("concrete").get("hc", 100000.0)
        res = run_engine(lat, lon, when, svf, gvi, ta, rh, v, shade, alb, emi, hc)
        tb, tg = float(res.mrt.tmrt), float(res.mrt.tmrt_globe)
        d = tg - tb                                   # 흑구 − 전신 (정의차)
        deltas.append(d)
        tmrt_meas_body = float(r["Tmrt"]) - d         # 전신 기준 실측 Tmrt
        pet_meas_body = pet_of(ta, tmrt_meas_body, res.pedestrian_wind_ms, rh, res.season)
        nr = dict(r)
        nr["Tmrt_흑구원본"] = r["Tmrt"]
        nr["PET_흑구원본"] = r["PET"]
        nr["정의차_흑구−전신"] = f"{d:.2f}"
        nr["Tmrt"] = f"{tmrt_meas_body:.2f}"          # ← 기준 교체
        nr["PET"] = f"{pet_meas_body:.2f}"            # ← 기준 교체
        nr["run_C_Tmrt"] = f"{tb:.2f}"
        nr["run_C_PET"] = f"{pet_of(ta, tb, res.pedestrian_wind_ms, rh, res.season):.2f}"
        out.append(nr)

    import statistics as st
    print(f"정의차 Δ(흑구−전신)  평균 {st.mean(deltas):+.2f}℃  중앙 {st.median(deltas):+.2f}  "
          f"최소 {min(deltas):+.2f}  최대 {max(deltas):+.2f}")
    print("  (2026-09-05 3단분해가 기록한 '+9.6℃ 정의차'와 대조할 것)")
    o = [float(x["PET_흑구원본"]) for x in out]
    n = [float(x["PET"]) for x in out]
    e = [float(x["run_C_PET"]) for x in out]
    print(f"기준 PET   흑구 기준 평균 {st.mean(o):.1f}  →  전신 기준 평균 {st.mean(n):.1f}  "
          f"({st.mean(n)-st.mean(o):+.1f}℃)")
    print(f"엔진 PET(새 설정) 평균 {st.mean(e):.1f}   "
          f"잔차(실측−엔진) 평균 {st.mean([a-b for a,b in zip(n,e)]):+.2f}  "
          f"MAE {st.mean([abs(a-b) for a,b in zip(n,e)]):.2f}")
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(f"\n저장: {dst}")
    print("다음: python3 scripts/train_pet_residual.py " + dst)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
