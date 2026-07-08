"""
vpti_core 데모 및 물리적 일관성 검증 스크립트.

실행:
    python -m vpti_core.demo          (backend/ 에서)

두 가지 일을 한다.
  1. 대표 시나리오(여름 도심 협곡 / 겨울 개방 광장)에 대해 VSI·SMTI·PWI·VPTI
     산출 과정을 단계별로 출력.
  2. 특허가 보장한다고 명시한 물리적 일관성을 assert 로 검증:
     · PWI > 0 (지수함수 부분보정계수 → 항상 양수, 수학식 5)
     · u_p ≥ 0 (보행자 풍속 음수 불가, 수학식 1)
     · Σ w_i = 1 (von Mises 가중, 수학식 3)
     · 모든 보정계수 C_h·C_z·C_ch > 0, 기하평균 안정성 (수학식 4)
     · Σ P_i = 1 (점유율 정규화, 수학식 6)
     · Δθ ∈ [0°, 90°] (도로축 정합, 수학식 2)
     · channeling: 평행(Δθ=0) > 수직(Δθ=90)
     · 무풍(u_ref=0) → u_p=0
     · AI 미제공 → Fallback (R_AI=1)
"""
from __future__ import annotations

import sys
from datetime import datetime

from .config import VPTICoreConfig
from .phi import Biometrics, PhysiologyProfile, compute_pvpti
from .pwi import build_horizontal_views, compute_pwi, road_axis_angle_diff
from .smti import MaterialFraction
from .vpti import WeatherContext, compute_vpti, compute_vpti_thermal
from .vsi import ViewSegmentation


def _summer_urban_canyon() -> dict:
    """여름 정오, 건물 밀집 도심 협곡 (아스팔트 보도, 약풍)."""
    views = [
        ViewSegmentation("up", sky_ratio=0.35, vegetation_ratio=0.05, building_ratio=0.60),
        ViewSegmentation("front", sky_ratio=0.10, vegetation_ratio=0.10, building_ratio=0.70),
        ViewSegmentation("back", sky_ratio=0.12, vegetation_ratio=0.08, building_ratio=0.68),
        ViewSegmentation("left", sky_ratio=0.05, vegetation_ratio=0.15, building_ratio=0.75),
        ViewSegmentation("right", sky_ratio=0.05, vegetation_ratio=0.12, building_ratio=0.78),
    ]
    materials = [
        MaterialFraction("asphalt", 0.60),
        MaterialFraction("concrete", 0.30),
        MaterialFraction("vegetation", 0.10),
    ]
    weather = WeatherContext(
        temperature_c=33.0, wind_speed_ms=3.0, wind_direction_deg=20.0, humidity_pct=60.0
    )
    return {
        "name": "여름 도심 협곡",
        "views": views,
        "materials": materials,
        "weather": weather,
        "road_axis_deg": 15.0,   # 풍향(20°)과 거의 평행 → channeling 기대
        "heading_deg": 15.0,
        "solar_intensity": 0.95,
    }


def _winter_open_plaza() -> dict:
    """겨울 오후, 개방된 광장 (잔디·콘크리트, 강풍, 풍향 도로축과 수직)."""
    views = [
        ViewSegmentation("up", sky_ratio=0.90, vegetation_ratio=0.02, building_ratio=0.08),
        ViewSegmentation("front", sky_ratio=0.40, vegetation_ratio=0.30, building_ratio=0.20),
        ViewSegmentation("back", sky_ratio=0.42, vegetation_ratio=0.28, building_ratio=0.18),
        ViewSegmentation("left", sky_ratio=0.45, vegetation_ratio=0.25, building_ratio=0.15),
        ViewSegmentation("right", sky_ratio=0.43, vegetation_ratio=0.27, building_ratio=0.16),
    ]
    materials = [
        MaterialFraction("concrete", 0.50),
        MaterialFraction("vegetation", 0.35),
        MaterialFraction("soil", 0.15),
    ]
    weather = WeatherContext(
        temperature_c=-4.0, wind_speed_ms=7.0, wind_direction_deg=110.0, humidity_pct=40.0
    )
    return {
        "name": "겨울 개방 광장",
        "views": views,
        "materials": materials,
        "weather": weather,
        "road_axis_deg": 20.0,   # 풍향(110°)과 거의 수직 → 차폐 기대
        "heading_deg": 20.0,
        "solar_intensity": 0.45,
    }


def _print_scenario(scn: dict) -> None:
    result = compute_vpti(
        views_5=scn["views"],
        materials=scn["materials"],
        weather=scn["weather"],
        road_axis_deg=scn["road_axis_deg"],
        heading_deg=scn["heading_deg"],
        solar_intensity=scn["solar_intensity"],
    )
    d = result.as_dict()
    print(f"\n{'=' * 64}\n■ 시나리오: {scn['name']}  (계절={d['season']})\n{'=' * 64}")
    print(f"  base_temp        : {d['base_temp']:.1f} °C")
    print(f"  VSI              : {d['vsi']['vsi']:.4f}  "
          f"(SVF={d['vsi']['svf']:.2f}, GVI={d['vsi']['gvi']:.2f}, BVI={d['vsi']['bvi']:.2f})")
    print(f"  SMTI             : {d['smti']['smti']:.4f}  "
          f"(α,β,γ={d['smti']['weights']['alpha']},{d['smti']['weights']['beta']},{d['smti']['weights']['gamma']}, "
          f"I={d['smti']['solar_intensity']:.2f}, σ={d['smti']['shading_coefficient']:.2f})")
    p = d["pwi"]
    print(f"  PWI              : {p['pwi']:.4f}  "
          f"(rule={p['pwi_rule']:.3f}, R_AI={p['ai_residual']:.2f}, fallback={p['used_fallback']})")
    print(f"    Δθ             : {p['delta_theta_deg']:.1f}°  "
          f"(C_h={p['c_horizontal']:.3f}, C_z={p['c_zenith']:.3f}, C_ch={p['c_channel']:.3f})")
    print(f"    u_ref → u_p    : {scn['weather'].wind_speed_ms:.1f} → "
          f"{p['pedestrian_wind_speed_ms']:.2f} m/s")
    print(f"    view_weights   : {p['view_weights']}")
    print(f"  Δ 분해 [°C]       : 공간 {d['contributions']['space']:+.2f} | "
          f"재질 {d['contributions']['material']:+.2f} | 바람 {d['contributions']['wind']:+.2f}")
    print(f"  ▶ VPTI           : {d['vpti']:.1f} °C   위험도={d['risk_level']}")


def _busan_summer_noon() -> dict:
    """부산 여름 한낮, 아스팔트 가로 (요구사항 ⑤ 데모 좌표)."""
    views = [
        ViewSegmentation("up", sky_ratio=0.45, vegetation_ratio=0.05, building_ratio=0.50),
        ViewSegmentation("front", sky_ratio=0.15, vegetation_ratio=0.12, building_ratio=0.65),
        ViewSegmentation("back", sky_ratio=0.18, vegetation_ratio=0.10, building_ratio=0.62),
        ViewSegmentation("left", sky_ratio=0.10, vegetation_ratio=0.18, building_ratio=0.68),
        ViewSegmentation("right", sky_ratio=0.12, vegetation_ratio=0.15, building_ratio=0.70),
    ]
    materials = [
        MaterialFraction("asphalt", 0.55),
        MaterialFraction("concrete", 0.30),
        MaterialFraction("vegetation", 0.15),
    ]
    weather = WeatherContext(
        temperature_c=31.0, wind_speed_ms=2.5, wind_direction_deg=200.0, humidity_pct=65.0
    )
    return {
        "name": "부산 여름 한낮 (35.18901, 129.10069)",
        "views": views,
        "materials": materials,
        "weather": weather,
        "road_axis_deg": 30.0,
        "heading_deg": 30.0,
        "lat": 35.18901,
        "lon": 129.10069,
        "when": datetime(2026, 7, 15, 14, 0),  # KST (tz-naive → config.solar.timezone)
        "sky_code": 1,  # KMA 맑음
    }


def _print_thermal_scenario(scn: dict, config: VPTICoreConfig) -> None:
    """MRT + UTCI/PET 경로 — 각 단계(일사 → MRT → UTCI) 중간값 전부 출력 (⑤)."""
    result = compute_vpti_thermal(
        views_5=scn["views"],
        materials=scn["materials"],
        weather=scn["weather"],
        road_axis_deg=scn["road_axis_deg"],
        lat=scn["lat"],
        lon=scn["lon"],
        when=scn["when"],
        sky_code=scn["sky_code"],
        heading_deg=scn["heading_deg"],
        config=config,
    )
    d = result.as_dict()
    w = scn["weather"]
    print(f"\n{'=' * 64}\n■ [MRT+UTCI] 시나리오: {scn['name']}\n{'=' * 64}")
    print(f"  시각/계절          : {scn['when']}  KST  /  {d['season']}")
    print(f"  기온/습도          : {w.temperature_c:.1f} °C  /  {w.humidity_pct:.0f} %")

    print("\n  ── 입력 지수 ──")
    print(f"  VSI                : {d['vsi']['vsi']:.4f}  "
          f"(SVF={d['vsi']['svf']:.2f}, GVI={d['vsi']['gvi']:.2f}, BVI={d['vsi']['bvi']:.2f})")
    sm = d["smti"]
    print(f"  SMTI               : {sm['smti']:.4f}  (I_norm={sm['solar_intensity']:.2f}, σ={sm['shading_coefficient']:.2f})")
    p = d["pwi"]
    print(f"  PWI                : {p['pwi']:.3f}  →  보행자 풍속 "
          f"{w.wind_speed_ms:.1f} → {d['pedestrian_wind_ms']:.2f} m/s")

    s = d["solar"]
    print(f"\n  ── ① 일사 추정 (pvlib + KMA SKY={s['sky_code']}, 운량={s['cloud_fraction']:.2f}) ──")
    print(f"  태양 고도/방위     : {s['solar_elevation_deg']:.1f}° / {s['solar_azimuth_deg']:.1f}°  "
          f"(주간={s['is_daytime']})")
    print(f"  청천 GHI           : {s['ghi_clearsky']:.0f} W/m²")
    print(f"  추정 GHI/DNI/DHI   : {s['ghi']:.0f} / {s['dni']:.0f} / {s['dhi']:.0f} W/m²")

    m = d["mrt"]
    print("\n  ── ② MRT (VDI 3787 6방향 복사속) ──")
    print(f"  지면 알베도/방사율 : {m['ground_albedo']:.3f} / {m['ground_emissivity']:.3f}  (SMTI 재질 도출)")
    print(f"  추정 지표면온도    : {m['ground_temp_c']:.1f} °C   천공 방사율 ε_sky={m['sky_emissivity']:.3f}")
    sw, lw = m["shortwave"], m["longwave"]
    print(f"  단파 흡수 [W/m²]   : 직달 {sw['direct']:.0f} + 산란 {sw['diffuse']:.0f} + 반사 {sw['reflected']:.0f}  (fp={sw['fp']:.3f})")
    print(f"  장파 흡수 [W/m²]   : 천공 {lw['sky']:.0f} + 지면 {lw['surface']:.0f}")
    print(f"  평균복사속 Sstr    : {m['sstr']:.0f} W/m²")
    print(f"  ▶ MRT (Tmrt)       : {m['tmrt']:.1f} °C")

    c = d["comfort"]
    print(f"\n  ── ③ 체감지수 ({c['index'].upper()}, pythermalcomfort) ──")
    ci = c["inputs"]
    print(f"  입력 (Tdb,Tr,v,RH) : {ci['tdb']:.1f} °C, {ci['tr']:.1f} °C, {ci['v']:.2f} m/s, {ci['rh']:.0f} %")
    print(f"  ▶ {c['index'].upper():<16} : {c['value']:.1f} °C   ({c['stress_category']})")

    print(f"\n  ▶▶ VPTI (= {d['comfort_index'].upper()}) : {d['vpti']:.1f} °C   위험도={d['risk_level']}")


def _print_busan_comparison(scn: dict) -> None:
    """동일 입력에 대해 가산형 vs MRT+UTCI 결과 비교 (④ 전환 확인)."""
    additive = compute_vpti(
        views_5=scn["views"], materials=scn["materials"], weather=scn["weather"],
        road_axis_deg=scn["road_axis_deg"], heading_deg=scn["heading_deg"],
        solar_intensity=0.95,
    )
    thermal = compute_vpti_thermal(
        views_5=scn["views"], materials=scn["materials"], weather=scn["weather"],
        road_axis_deg=scn["road_axis_deg"], lat=scn["lat"], lon=scn["lon"],
        when=scn["when"], sky_code=scn["sky_code"], heading_deg=scn["heading_deg"],
    )
    print(f"\n{'=' * 64}\n■ 통합 모드 비교 (동일 입력, 부산)\n{'=' * 64}")
    print(f"  기온(base)         : {scn['weather'].temperature_c:.1f} °C")
    print(f"  가산형(additive)   : VPTI {additive.vpti:.1f} °C  위험도={additive.risk_level}")
    print(f"  MRT+UTCI(기본)     : VPTI {thermal.vpti:.1f} °C  위험도={thermal.risk_level}  "
          f"(MRT={thermal.mrt.tmrt:.1f} °C)")


def _print_pvpti_scenario(scn: dict) -> None:
    """PHI 생리 개인화 — 동일 환경에서 생체신호(애플워치)만 바꿔 pVPTI 비교 (⑥).

    activity → met 로 PET 개인화(base→pVPTI), 잔차 심박부하(strain)만 위험경계 앞당김.
    activity 가 없으면 strain=0 으로 억제(환경 PET 만 반영).
    """
    thermal = {
        "views_5": scn["views"],
        "materials": scn["materials"],
        "weather": scn["weather"],
        "road_axis_deg": scn["road_axis_deg"],
        "heading_deg": scn["heading_deg"],
        "lat": scn["lat"],
        "lon": scn["lon"],
        "when": scn["when"],
        "sky_code": scn["sky_code"],
    }
    profile = PhysiologyProfile(age=40, sex="male", height_cm=175, weight_kg=72)
    cases = [
        ("안정(앉음)",          Biometrics(hr=68, activity=1.4, hr_rest=60)),
        ("활발한 걸음",          Biometrics(hr=118, activity=5.5, hr_rest=60)),
        ("가벼운 활동+심박초과", Biometrics(hr=165, activity=3.0, hr_rest=60)),
        ("activity 없음",       Biometrics(hr=150, activity=None, hr_rest=60)),
    ]

    w = scn["weather"]
    print(f"\n{'=' * 64}\n■ [PHI] pVPTI 생리 개인화: {scn['name']}\n{'=' * 64}")
    print(f"  환경/기온          : {w.temperature_c:.1f} °C  (같은 장소·시각, 생체신호만 변경)")
    print("  프로필             : 40세 남 175cm 72kg  hr_rest=60")
    print(f"  {'케이스':<18}{'met':>6}{'obsHRR':>8}{'expHRR':>8}"
          f"{'strain':>8}{'base':>7}{'pVPTI':>8}  위험도")
    print(f"  {'-' * 60}")
    for name, bio in cases:
        r = compute_pvpti(bio=bio, profile=profile, **thermal)
        met = f"{r.metabolic_met:.2f}" if r.metabolic_met is not None else "  —"
        obs = f"{r.observed_hrr:.2f}" if r.observed_hrr is not None else "  —"
        exp = f"{r.expected_hrr:.2f}" if r.expected_hrr is not None else "  —"
        print(f"  {name:<18}{met:>6}{obs:>8}{exp:>8}{r.strain_index:>8.2f}"
              f"{r.base_vpti:>7.1f}{r.pvpti:>8.1f}  {r.base_risk_level}→{r.risk_level}")


def _check(label: str, condition: bool) -> bool:
    mark = "✅" if condition else "❌"
    print(f"  {mark} {label}")
    return condition


def run_consistency_checks() -> bool:
    print(f"\n{'=' * 64}\n■ 물리적 일관성 검증\n{'=' * 64}")
    ok = True

    # --- PWI 핵심 일관성 (여름 시나리오 사용) ---
    scn = _summer_urban_canyon()
    res = compute_vpti(
        views_5=scn["views"], materials=scn["materials"], weather=scn["weather"],
        road_axis_deg=scn["road_axis_deg"], heading_deg=scn["heading_deg"],
        solar_intensity=scn["solar_intensity"],
    )
    pwi = res.pwi

    ok &= _check("PWI > 0 (수학식 5 지수함수 → 항상 양수)", pwi.pwi > 0.0)
    ok &= _check("u_p ≥ 0 (수학식 1, 보행자 풍속 음수 불가)", pwi.pedestrian_wind_speed_ms >= 0.0)
    ok &= _check("모든 부분 보정계수 > 0 (C_h, C_z, C_ch)",
                 min(pwi.c_horizontal, pwi.c_zenith, pwi.c_channel) > 0.0)

    w_sum = sum(pwi.view_weights.values())
    ok &= _check(f"Σ w_i = 1 (von Mises, 수학식 3) → {w_sum:.6f}", abs(w_sum - 1.0) < 1e-9)
    ok &= _check("모든 w_i ∈ [0, 1]", all(0.0 <= w <= 1.0 for w in pwi.view_weights.values()))

    # 기하평균 안정성: 세 계수의 min ≤ 기하평균 ≤ max (수학식 4)
    cs = [pwi.c_horizontal, pwi.c_zenith, pwi.c_channel]
    ok &= _check(f"기하평균 안정성: min ≤ rule ≤ max ({min(cs):.3f} ≤ {pwi.pwi_rule:.3f} ≤ {max(cs):.3f})",
                 min(cs) - 1e-9 <= pwi.pwi_rule <= max(cs) + 1e-9)

    # Δθ 범위 (수학식 2)
    ok &= _check(f"Δθ ∈ [0°, 90°] → {pwi.delta_theta_deg:.1f}°",
                 0.0 <= pwi.delta_theta_deg <= 90.0)

    # --- SMTI: Σ P_i = 1 (수학식 6) ---
    p_sum = sum(m.fraction for m in res.smti.per_material)
    ok &= _check(f"Σ P_i = 1 (수학식 6) → {p_sum:.6f}", abs(p_sum - 1.0) < 1e-9)

    # --- Δθ 단조성: 평행(0°) vs 수직(90°) channeling 비교 ---
    from .config import DEFAULT_CONFIG
    from .pwi import _channeling  # noqa: PLC2701 (검증 목적의 내부 함수 직접 호출)
    c_parallel = _channeling(0.0, DEFAULT_CONFIG.pwi)
    c_perp = _channeling(90.0, DEFAULT_CONFIG.pwi)
    ok &= _check(f"channeling: 평행(Δθ=0)={c_parallel:.3f} > 수직(Δθ=90)={c_perp:.3f}",
                 c_parallel > c_perp)
    ok &= _check("평행이면 channeling 증폭(>1), 수직이면 감쇠(<1)",
                 c_parallel > 1.0 > c_perp)

    # --- 수학식 2 정규화 자체 검증 ---
    ok &= _check("Δθ(평행 0°/180°)=0", abs(road_axis_angle_diff(15.0, 15.0)) < 1e-9
                 and abs(road_axis_angle_diff(195.0, 15.0)) < 1e-9)
    ok &= _check("Δθ(수직 90°)=90", abs(road_axis_angle_diff(105.0, 15.0) - 90.0) < 1e-9)

    # --- 무풍 → u_p = 0 ---
    calm_views = build_horizontal_views(
        0.0, {"F": 0.3, "R": 0.3, "B": 0.3, "L": 0.3}, {"F": 0.1, "R": 0.1, "B": 0.1, "L": 0.1}
    )
    calm = compute_pwi(
        wind_speed_ms=0.0, wind_direction_deg=0.0, road_axis_deg=0.0, svf=0.5,
        horizontal_views=calm_views,
    )
    ok &= _check("무풍(u_ref=0) → u_p=0", calm.pedestrian_wind_speed_ms == 0.0)
    ok &= _check("무풍이어도 PWI>0 유지", calm.pwi > 0.0)

    # --- Fallback: AI 미제공 → R_AI=1 ---
    ok &= _check("AI 미제공 → Fallback (R_AI=1)", pwi.used_fallback and pwi.ai_residual == 1.0)

    # --- AI 신뢰도에 따른 Fallback 분기 ---
    canyon_views = build_horizontal_views(
        15.0, {"F": 0.7, "R": 0.78, "B": 0.68, "L": 0.75},
        {"F": 0.1, "R": 0.12, "B": 0.08, "L": 0.15},
    )
    low_conf = compute_pwi(
        wind_speed_ms=3.0, wind_direction_deg=20.0, road_axis_deg=15.0, svf=0.35,
        horizontal_views=canyon_views,
        ai_residual=1.5, ai_confidence=0.2,  # 신뢰도 0.2 < 임계 0.5
    )
    ok &= _check("AI 저신뢰(conf=0.2<0.5) → Fallback", low_conf.used_fallback)

    high_conf = compute_pwi(
        wind_speed_ms=3.0, wind_direction_deg=20.0, road_axis_deg=15.0, svf=0.35,
        horizontal_views=canyon_views,
        ai_residual=1.2, ai_confidence=0.9,
    )
    ok &= _check("AI 고신뢰(conf=0.9) → R_AI 잔차 반영(≈1.2)",
                 (not high_conf.used_fallback) and abs(high_conf.ai_residual - 1.2) < 1e-9)

    return ok


def main() -> int:
    print("vpti_core 데모 — VSI·SMTI·PWI 특허 수식 참조 구현")

    print(f"\n{'#' * 64}\n# 가산형 (additive) 경로 — 기존 비교용\n{'#' * 64}")
    for scn in (_summer_urban_canyon(), _winter_open_plaza()):
        _print_scenario(scn)

    print(f"\n{'#' * 64}\n# MRT + UTCI/PET 경로 — 재설계 기본\n{'#' * 64}")
    busan = _busan_summer_noon()
    # UTCI (기본)
    _print_thermal_scenario(busan, VPTICoreConfig())
    # PET 도 확인 (config.comfort.index='pet')
    from dataclasses import replace

    pet_config = VPTICoreConfig()
    pet_config = replace(pet_config, comfort=replace(pet_config.comfort, index="pet"))
    _print_thermal_scenario(busan, pet_config)
    # 가산형 vs 물리기반 비교
    _print_busan_comparison(busan)

    print(f"\n{'#' * 64}\n# PHI 생리 개인화 (애플워치 → pVPTI)\n{'#' * 64}")
    _print_pvpti_scenario(busan)

    all_ok = run_consistency_checks()
    print(f"\n{'=' * 64}")
    if all_ok:
        print("■ 결과: 모든 물리적 일관성 검증 통과 ✅")
        return 0
    print("■ 결과: 일부 검증 실패 ❌")
    return 1


if __name__ == "__main__":
    sys.exit(main())
