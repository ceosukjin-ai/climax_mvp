#!/usr/bin/env python3
"""벽 스테이지 재검증 — 실제 외벽재질 적재 후 (2026-09-13).

왜 다시 재나:
  벽 스테이지(GEO_WALL_STAGE)는 꺼져 있다. 껐던 근거는 config.py 주석 —
  "폴백 '벽=지면온도'(55°C)보다 낮아 MAE 3.5→4.6 악화".
  그런데 그때는 **전국 건물이 전부 콘크리트(알베도 0.30)** 라는 가정이었다.
  오늘 표제부 구조를 적재해 목조·벽돌·철골이 실제로 구분된다. 그 판단이 아직 유효한지 다시 본다.

세 가지를 같은 80점·같은 기상으로 비교한다:
  C        벽 스테이지 없음 (현행 배포 상태)
  E-con    벽 스테이지 켬, 재질 콘크리트 고정 (예전 시험과 동일 조건)
  E-mat    벽 스테이지 켬, 재질은 좌표별 실제값 (dominant_wall_material)

E-mat 이 E-con 보다 나아지지 않으면 재질이 원인이 아니었던 것이고, 스테이지는 계속 꺼 둔다.
E-mat 이 C 보다 나아져야만 스테이지를 켤 근거가 생긴다.

  docker exec -i climax-api python3 /repo/scripts/run_tier3_wall_mat.py
"""
from __future__ import annotations
import asyncio, csv, math, statistics, sys
from datetime import datetime
sys.path.insert(0, "/app"); sys.path.insert(0, "/repo/backend")

from vpti_core import DEFAULT_CONFIG
from vpti_core.solar import estimate_solar
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet
from vpti_core.mrt import (estimate_wall_temp, estimate_wall_temp_transient,
                           estimate_ground_temp, sky_emissivity)
from datetime import timedelta
from app.services.geo import dominant_wall_material

IN = "/repo/data/tier3_engine_output_80_v7.csv"
MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]
GROUND_ALBEDO, GROUND_EMISSIVITY = 0.155, 0.94   # asphalt .7 / concrete .3 면적가중
from app.services.wall_material import props_for as _props   # noqa: E402
_CONC_HC = _props("concrete").get("hc", 100000.0)
FN = {"N": 180.0, "E": 270.0, "S": 0.0, "W": 90.0}   # look 방향이 보는 파사드 법선 = d+180


def views(svf, gvi):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi, building_ratio=b)
           for d in ("front", "back", "left", "right")]
    return vs


def one(r, mode, alb, emi, refl=False, gnd=False, trans=False, hc=None):
    lat, lon = float(r["위도"]), float(r["경도"])
    when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")
    shade = 1.0 if str(r["볕"]).strip() == "1" else 0.0
    ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"])
    svf, gvi = float(r["tier3_svf"]), float(r["tier3_gvi"])
    sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
    wall = None
    if mode != "C" and svf < 0.92 and sol.solar_elevation_deg > 0:
        epsk = sky_emissivity(ta, rh, sol.cloud_fraction, DEFAULT_CONFIG.mrt)
        # 노면온도·노면알베도 — 벽이 아래 절반으로 보는 면. gnd=False 면 기존(기온짜리 주변).
        _ga, _ge = GROUND_ALBEDO, GROUND_EMISSIVITY
        _tg = None
        if gnd:
            _tg = estimate_ground_temp(ta, sol, _ga, _ge, svf, gvi, v, epsk,
                                       DEFAULT_CONFIG.mrt, shade, None)
        # 과도해석용 과거 12h forcing — 배포 경로(routes.py)와 같은 방식.
        # 소급시험이라 과거 기온 관측이 없어 측정 Ta 를 유지한다(지배요인은 일사 이력이다).
        _ser = None
        if trans and hc:
            _ser = []
            for _h in range(12, -1, -1):
                _t = when - timedelta(hours=_h)
                _s = estimate_solar(lat, lon, _t, config=DEFAULT_CONFIG.solar)
                _ek = sky_emissivity(ta, rh, _s.cloud_fraction, DEFAULT_CONFIG.mrt)
                _tgh = (estimate_ground_temp(ta, _s, _ga, _ge, svf, gvi, v, _ek,
                                             DEFAULT_CONFIG.mrt, shade, None) if gnd else None)
                _row = [_h * 3600.0, ta, _s.dni, _s.dhi,
                        _s.solar_elevation_deg, _s.solar_azimuth_deg]
                if gnd:
                    _row += [_tgh, _ga]
                _ser.append(tuple(_row))
        wall = {}
        for d, fn in FN.items():
            cosf = max(0.0, math.cos(math.radians(sol.solar_azimuth_deg - fn)))
            wt = None
            if _ser:
                wt = estimate_wall_temp_transient(_ser, alb, emi, cosf, v, epsk, hc,
                                                  DEFAULT_CONFIG.mrt, facade_normal_deg=fn)
            if wt is None:
                wt = estimate_wall_temp(ta, sol, alb, emi, cosf, v, epsk, DEFAULT_CONFIG.mrt,
                                        ground_temp_c=_tg,
                                        ground_albedo=(_ga if gnd else None))
            wall[d] = wt
    wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v, wind_direction_deg=0.0)
    res = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc,
                               road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                               direct_shade=shade, wall_temp_c=wall,
                               wall_albedo=(alb if refl else None),
                               wind_is_pedestrian=True)
    tm = float(res.mrt.tmrt)                 # 전신(사람) Tmrt — 앱이 쓰는 값
    tg = float(res.mrt.tmrt_globe)           # 흑구가 읽었을 Tmrt — 실측과 비교할 값
    def _pet(tr):
        return float(compute_pet(tdb=ta, tr=tr, v=res.pedestrian_wind_ms, rh=rh,
                                 season=res.season, config=DEFAULT_CONFIG.comfort).value)
    w = None if wall is None else sum(wall.values()) / len(wall)
    return tm, _pet(tm), w, tg, _pet(tg)


def stat(name, errs, tm_errs, hot, hit, walls):
    if not errs:
        print(f"  {name:8} 표본 없음"); return
    mae = sum(map(abs, errs)) / len(errs)
    bias = sum(errs) / len(errs)
    line = (f"  {name:8} n={len(errs):3}  PET MAE {mae:5.2f}  bias {bias:+5.2f}"
            f"   Tmrt bias {sum(tm_errs)/len(tm_errs):+5.2f}   극심 {hit}/{hot}")
    if walls:
        line += f"   벽온도 {statistics.mean(walls):5.1f}℃"
    print(line)


async def main():
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    print(f"입력 {len(rows)}행  {IN}\n")

    # 좌표별 실제 재질 (같은 좌표가 반복되므로 캐시)
    cache: dict[tuple, dict] = {}
    mats = []
    for r in rows:
        k = (round(float(r["위도"]), 5), round(float(r["경도"]), 5))
        if k not in cache:
            cache[k] = await dominant_wall_material(k[0], k[1])
        mats.append(cache[k])
    cnt: dict[str, int] = {}
    for m in cache.values():
        cnt[m["material"]] = cnt.get(m["material"], 0) + 1
    print(f"80점 좌표 {len(cache)}곳의 대표 외벽재질: {cnt}")
    albs = [m["albedo"] for m in cache.values()]
    print(f"  알베도 평균 {statistics.mean(albs):.3f} (콘크리트 고정값 0.300)   "
          f"최소 {min(albs):.3f} 최대 {max(albs):.3f}\n")

    truth_p = [float(r["PET"]) for r in rows]
    truth_t = [float(r["Tmrt"]) for r in rows]
    sun = [str(r["볕"]).strip() == "1" for r in rows]

    print("=== 실측 대비 (run − 실측) ===")
    MODES = (
        ("C",    "C 현행",  False, False, False, False),  # 벽 스테이지 없음 (배포 상태)
        ("Rmat", "R-mat",  False, True,  False, False),  # 단파반사만 (배포 결정됨)
        ("Gmat", "G-mat",  True,  True,  True,  False),  # 정상상태 벽온도 + 노면 봄
        ("Tmat", "T-mat",  True,  True,  True,  True),   # **과도(열질량)** 벽온도 ← 배포 경로와 동일
        ("Tcon", "T-con",  True,  True,  True,  True),   # 같은 조건, 재질만 콘크리트 고정
    )
    for mode, label, use_wall, use_refl, use_gnd, use_tr in MODES:
        res = []
        for i, r in enumerate(rows):
            if mode in ("Rmat", "Gmat", "Tmat"):
                a, e, hc = mats[i]["albedo"], mats[i]["emissivity"], mats[i].get("hc")
            else:
                a, e, hc = 0.30, 0.90, _CONC_HC
            res.append(one(r, "E" if use_wall else "C", a, e,
                           refl=use_refl, gnd=use_gnd, trans=use_tr, hc=hc))
        # 실측 Tmrt·PET 는 흑구(Ø0.05m) 기반이다 → 엔진도 흑구 예측값으로 비교한다.
        ep = [res[i][4] - truth_p[i] for i in range(len(rows))]
        et = [res[i][3] - truth_t[i] for i in range(len(rows))]
        walls = [r[2] for r in res if r[2] is not None]
        hot = sum(1 for i in range(len(rows)) if truth_p[i] >= 41)
        hit = sum(1 for i in range(len(rows)) if truth_p[i] >= 41 and res[i][4] >= 41)
        print(f"[{label}]")
        stat("전체", ep, et, hot, hit, walls)
        si = [i for i in range(len(rows)) if sun[i]]
        hi = [i for i in range(len(rows)) if not sun[i]]
        stat("양지", [ep[i] for i in si], [et[i] for i in si],
             sum(1 for i in si if truth_p[i] >= 41),
             sum(1 for i in si if truth_p[i] >= 41 and res[i][4] >= 41),
             [res[i][2] for i in si if res[i][2] is not None])
        stat("그늘", [ep[i] for i in hi], [et[i] for i in hi],
             sum(1 for i in hi if truth_p[i] >= 41),
             sum(1 for i in hi if truth_p[i] >= 41 and res[i][4] >= 41),
             [res[i][2] for i in hi if res[i][2] is not None])
        _tb = [r[0] for r in res]; _tg = [r[3] for r in res]
        _pb = [r[1] for r in res]; _pg = [r[4] for r in res]
        print(f"    앱값(전신) Tmrt 평균 {sum(_tb)/len(_tb):5.1f}  PET 평균 {sum(_pb)/len(_pb):5.1f}"
              f"   |  흑구예측 Tmrt {sum(_tg)/len(_tg):5.1f}  PET {sum(_pg)/len(_pg):5.1f}"
              f"   (흑구−전신 {sum(_tg)/len(_tg)-sum(_tb)/len(_tb):+.1f}℃)")
    print()
    print("판정 (2026-09-13 기준 정리 후):")
    print("  · 위 표는 전부 **흑구 예측 vs 흑구 실측** 비교다. 정의차가 제거됐다.")
    print("  · G-mat/T-mat 이 C 보다 나으면 → 벽 스테이지를 켠다. 이번엔 사과 대 사과다.")
    print("  · 괄호의 (흑구−전신) 이 곧 그동안 상쇄에 쓰이던 정의차다.")


if __name__ == "__main__":
    asyncio.run(main())
