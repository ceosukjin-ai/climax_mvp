"""
VPTI (Visualized Personal Thermal Index) — 통합 체감 기후 지수 코어 엔진.

세 특허(VSI·SMTI·PWI)의 산출 결과를 하나의 체감 기후 지수로 융합한다.

⚠️ 중요 — 결합 수식은 특허 미규정:
  APE-2026-0656(SMTI 명세서) 23~24쪽은 "표면 재질 기반 열 지수에 VSI와 PWI를
  적용하여 통합된 체감 기후 지수(VPTI)를 생성한다"고만 기술할 뿐, 구체적인
  결합 수식·가중치를 제시하지 않는다. 따라서 본 모듈의 가산형 체감기온 모델

      VPTI = base_temp + Δ_VSI(공간) + Δ_SMTI(재질) + Δ_PWI(바람)

  과 계절별 스케일은 전부 ⚠️ UNCONFIRMED(가정값, config.VPTIConfig)이며 실증
  데이터로 대체되어야 한다. 세 지수 각각(vsi/smti/pwi.py)은 특허 수식을 원문
  그대로 구현하므로, 본 엔진은 그 결과를 "조립"하는 층이다.

엔진은 5-view 세그멘테이션 하나로 세 지수의 입력을 모두 유도한다:
  · VSI  ← 5-view 면적 비율
  · PWI  ← 상향뷰 SVF + 수평 4뷰 BVI·GVI + 풍향/도로축
  · SMTI ← 재질 점유율 + 일사 + 음영(σ = 1 − SVF)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .comfort import ComfortResult, compute_comfort
from .config import DEFAULT_CONFIG, Season, VPTICoreConfig
from .materials import get_properties
from .mrt import MRTResult, compute_mrt, ground_properties_from_materials
from .pwi import PWIResult, build_horizontal_views, compute_pwi
from .smti import MaterialFraction, SMTIResult, compute_smti, shading_from_svf
from .solar import SolarResult, estimate_solar
from .vsi import VSIResult, ViewSegmentation, compute_vsi

RiskLevel = Literal["safe", "caution", "warning", "danger", "severe"]


@dataclass(frozen=True, slots=True)
class WeatherContext:
    """기상청 관측 기상 조건."""

    temperature_c: float
    wind_speed_ms: float        # u_ref @ 관측소
    wind_direction_deg: float   # θ_wind
    humidity_pct: float = 50.0

    def season(self, config: VPTICoreConfig = DEFAULT_CONFIG) -> Season:
        if self.temperature_c >= config.vpti.summer_temp_threshold:
            return "summer"
        if self.temperature_c <= config.vpti.winter_temp_threshold:
            return "winter"
        return "transition"


@dataclass(frozen=True, slots=True)
class VPTIResult:
    vpti: float                  # 통합 체감 기온 [°C]
    risk_level: RiskLevel
    season: Season
    base_temp: float

    vsi: VSIResult
    smti: SMTIResult
    pwi: PWIResult

    # 원인 분해 [°C]
    delta_space: float
    delta_material: float
    delta_wind: float

    def as_dict(self) -> dict:
        return {
            "vpti": round(self.vpti, 2),
            "risk_level": self.risk_level,
            "season": self.season,
            "base_temp": round(self.base_temp, 2),
            "contributions": {
                "space": round(self.delta_space, 2),
                "material": round(self.delta_material, 2),
                "wind": round(self.delta_wind, 2),
            },
            "vsi": self.vsi.as_dict(),
            "smti": self.smti.as_dict(),
            "pwi": self.pwi.as_dict(),
        }


def _seasonal(summer: float, winter: float, season: Season) -> float:
    if season == "summer":
        return summer
    if season == "winter":
        return winter
    return (summer + winter) / 2.0


def _wind_cooling_factor(pedestrian_wind_ms: float) -> float:
    """보행자 풍속 → [0, 1] 냉각 강도. tanh 포화 (10 m/s ≈ 0.76).

    ⚠️ UNCONFIRMED — 풍속→체감 변환 곡선은 특허 외 영역. 강풍일수록 냉각이
    포화하는 물리적 직관을 tanh로 근사.
    """
    import math

    return math.tanh(max(pedestrian_wind_ms, 0.0) / 10.0)


def _classify_risk(vpti: float, season: Season) -> RiskLevel:
    """계절별 체감기후 위험도 (기상청·질병청 온열/한랭 가이드 참고, ⚠️ UNCONFIRMED 임계)."""
    if season == "summer":
        if vpti >= 38.0:
            return "severe"
        if vpti >= 35.0:
            return "danger"
        if vpti >= 32.0:
            return "warning"
        if vpti >= 28.0:
            return "caution"
        return "safe"
    if season == "winter":
        if vpti <= -18.0:
            return "severe"
        if vpti <= -12.0:
            return "danger"
        if vpti <= -5.0:
            return "warning"
        if vpti <= 3.0:
            return "caution"
        return "safe"
    if vpti >= 32.0 or vpti <= -5.0:
        return "warning"
    if vpti >= 28.0 or vpti <= 3.0:
        return "caution"
    return "safe"


def compute_vpti(
    views_5: list[ViewSegmentation],
    materials: list[MaterialFraction],
    weather: WeatherContext,
    road_axis_deg: float,
    heading_deg: float = 0.0,
    solar_intensity: float = 0.0,
    ai_residual: float | None = None,
    ai_confidence: float | None = None,
    config: VPTICoreConfig = DEFAULT_CONFIG,
) -> VPTIResult:
    """VPTI 통합 산출.

    Args:
        views_5: 5-view 세그멘테이션 (front/back/left/right/up).
        materials: 재질 점유율 리스트 (SMTI용).
        weather: 기상청 관측값.
        road_axis_deg: 도로축 방향 [deg] (PWI 수학식 2용).
        heading_deg: 보행자 진행 방향 [deg]. 수평 4뷰 절대 방위각 산출에 사용.
        solar_intensity: 정규화 일사량 I ∈ [0, 1] (SMTI용). 야간이면 0.
        ai_residual, ai_confidence: PWI 2차 AI 보정 (없으면 Fallback).
        config: 전역 설정.

    Returns:
        VPTIResult — 통합 값 + 세 지수 결과 + 원인 분해.
    """
    season = weather.season(config)

    # --- VSI (수학식 1) ---
    vsi = compute_vsi(views_5, config.vsi)

    # --- PWI (수학식 1~5) — 5-view에서 SVF·BVI·GVI 유도 ---
    by_dir = {v.direction: v for v in views_5}
    label_to_dir = {"F": "front", "R": "right", "B": "back", "L": "left"}
    bvi_by_label = {lab: by_dir[d].building_ratio for lab, d in label_to_dir.items()}
    gvi_by_label = {lab: by_dir[d].vegetation_ratio for lab, d in label_to_dir.items()}
    horizontal_views = build_horizontal_views(heading_deg, bvi_by_label, gvi_by_label)
    pwi = compute_pwi(
        wind_speed_ms=weather.wind_speed_ms,
        wind_direction_deg=weather.wind_direction_deg,
        road_axis_deg=road_axis_deg,
        svf=vsi.svf,
        horizontal_views=horizontal_views,
        ai_residual=ai_residual,
        ai_confidence=ai_confidence,
        config=config.pwi,
    )

    # --- SMTI (수학식 1~6) — 음영 σ = 1 − SVF ---
    shading = shading_from_svf(vsi.svf)
    smti = compute_smti(
        materials=materials,
        solar_intensity=solar_intensity,
        shading_coefficient=shading,
        season=season,
        config=config.smti,
    )

    # --- 가산형 융합 (⚠️ UNCONFIRMED 결합 모델) ---
    vc = config.vpti
    delta_space = _seasonal(vc.vsi_summer_delta, vc.vsi_winter_delta, season) * _clip01(vsi.vsi)
    delta_material = _seasonal(vc.smti_summer_delta, vc.smti_winter_delta, season) * _clip01(smti.smti)
    delta_wind = _seasonal(vc.pwi_summer_delta, vc.pwi_winter_delta, season) * _wind_cooling_factor(
        pwi.pedestrian_wind_speed_ms
    )

    base_temp = weather.temperature_c
    vpti_value = base_temp + delta_space + delta_material + delta_wind
    risk = _classify_risk(vpti_value, season)

    return VPTIResult(
        vpti=vpti_value,
        risk_level=risk,
        season=season,
        base_temp=base_temp,
        vsi=vsi,
        smti=smti,
        pwi=pwi,
        delta_space=delta_space,
        delta_material=delta_material,
        delta_wind=delta_wind,
    )


def _clip01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


# =============================================================================
# MRT + UTCI/PET 경로 (재설계 기본 통합 모드)
# =============================================================================
@dataclass(frozen=True, slots=True)
class ThermalVPTIResult:
    """물리 기반 VPTI — UTCI/PET 가 곧 통합 체감 기후 지수."""

    vpti: float                  # 통합 체감지수 [°C] = UTCI 또는 PET
    comfort_index: Literal["utci", "pet"]
    risk_level: RiskLevel
    stress_category: str         # 표준 열스트레스 등급
    season: Season
    base_temp: float             # 기온 Ta [°C]
    pedestrian_wind_ms: float    # PWI 적용 보행자 풍속 u_p

    # 단계별 중간 결과
    solar: SolarResult
    mrt: MRTResult
    comfort: ComfortResult
    vsi: VSIResult
    smti: SMTIResult
    pwi: PWIResult

    def as_dict(self) -> dict:
        return {
            "vpti": round(self.vpti, 2),
            "comfort_index": self.comfort_index,
            "risk_level": self.risk_level,
            "stress_category": self.stress_category,
            "season": self.season,
            "base_temp": round(self.base_temp, 2),
            "pedestrian_wind_ms": round(self.pedestrian_wind_ms, 2),
            "solar": self.solar.as_dict(),
            "mrt": self.mrt.as_dict(),
            "comfort": self.comfort.as_dict(),
            "vsi": self.vsi.as_dict(),
            "smti": self.smti.as_dict(),
            "pwi": self.pwi.as_dict(),
        }


def _classify_risk_thermal(value: float, index: Literal["utci", "pet"]) -> RiskLevel:
    """표준 체감지수 → 5단계 위험도.

    UTCI: Bröde(2012) 10단계 열스트레스 평가척도를 5단계로 축약.
    PET : Matzarakis & Mayer(1996) 등급을 5단계로 축약.
    더위·추위 양방향 대칭. 임의값이 아니라 표준 평가척도 경계를 그대로 사용.
    """
    if index == "pet":
        # PET [°C] 경계 (Matzarakis & Mayer 1996)
        if value >= 41.0 or value < 4.0:
            return "severe"
        if value >= 35.0 or value < 8.0:
            return "danger"
        if value >= 29.0 or value < 13.0:
            return "warning"
        if value >= 23.0 or value < 18.0:
            return "caution"
        return "safe"
    # UTCI [°C] 평가척도 (Bröde 2012)
    if value >= 38.0 or value <= -27.0:    # very strong/extreme stress
        return "severe"
    if value >= 32.0 or value <= -13.0:    # strong stress
        return "danger"
    if value >= 26.0 or value <= 0.0:      # moderate stress
        return "warning"
    if value < 9.0:                        # slight cold stress (0~9)
        return "caution"
    return "safe"                          # no thermal stress (9~26)


def compute_vpti_thermal(
    views_5: list[ViewSegmentation],
    materials: list[MaterialFraction],
    weather: WeatherContext,
    road_axis_deg: float,
    lat: float,
    lon: float,
    when: datetime,
    sky_code: int | None = None,
    cloud_fraction: float | None = None,
    heading_deg: float = 0.0,
    ai_residual: float | None = None,
    ai_confidence: float | None = None,
    config: VPTICoreConfig = DEFAULT_CONFIG,
) -> ThermalVPTIResult:
    """물리 기반 VPTI — 일사 → MRT → UTCI/PET.

    가산형(compute_vpti)과 달리 체감지수가 표준 열생리 모델(UTCI/PET)로
    환원되므로, VSI·SMTI·PWI 의 출력이 물리량(SVF·알베도·보행자 풍속 등)으로
    각 단계에 직접 들어간다.

    파이프라인:
        ① solar  = estimate_solar(lat, lon, when, SKY)        → GHI/DNI/DHI
        ② mrt    = compute_mrt(solar, Ta, SVF, GVI, α, ε)     → Tmrt
        ③ comfort= compute_comfort(Ta, Tmrt, u_p(PWI), RH)    → UTCI/PET
        VPTI = comfort.value

    Args:
        views_5, materials, weather, road_axis_deg, heading_deg: 가산형과 동일.
        lat, lon: 위경도 [deg] (태양위치·일사 계산).
        when: 평가 시각 (tz-naive 면 config.solar.timezone 으로 간주).
        sky_code: KMA SKY 코드(1/3/4) — 운량 감쇠.
        cloud_fraction: 전운량 비율 직접 지정(있으면 sky_code 우선).
        ai_residual, ai_confidence: PWI 2차 AI 보정.
        config: 전역 설정.
    """
    season = weather.season(config)

    # --- VSI (수학식 1) ---
    vsi = compute_vsi(views_5, config.vsi)

    # --- PWI → 보행자 풍속 u_p ---
    by_dir = {v.direction: v for v in views_5}
    label_to_dir = {"F": "front", "R": "right", "B": "back", "L": "left"}
    bvi_by_label = {lab: by_dir[d].building_ratio for lab, d in label_to_dir.items()}
    gvi_by_label = {lab: by_dir[d].vegetation_ratio for lab, d in label_to_dir.items()}
    horizontal_views = build_horizontal_views(heading_deg, bvi_by_label, gvi_by_label)
    pwi = compute_pwi(
        wind_speed_ms=weather.wind_speed_ms,
        wind_direction_deg=weather.wind_direction_deg,
        road_axis_deg=road_axis_deg,
        svf=vsi.svf,
        horizontal_views=horizontal_views,
        ai_residual=ai_residual,
        ai_confidence=ai_confidence,
        config=config.pwi,
    )

    # --- ① 일사 추정 ---
    solar = estimate_solar(lat, lon, when, sky_code=sky_code,
                           cloud_fraction=cloud_fraction, config=config.solar)

    # --- 재질 → 지면 알베도·방사율 (SMTI DB 연결) ---
    ground_albedo, ground_emissivity = ground_properties_from_materials(
        materials, get_properties
    )

    # --- SMTI (진단·해석용) — 정규화 일사 I = GHI/GHI_ref ---
    solar_intensity = _clip01(solar.ghi / config.mrt.ghi_reference)
    smti = compute_smti(
        materials=materials,
        solar_intensity=solar_intensity,
        shading_coefficient=shading_from_svf(vsi.svf),
        season=season,
        config=config.smti,
    )

    # --- ② MRT ---
    mrt = compute_mrt(
        solar=solar,
        air_temp_c=weather.temperature_c,
        humidity_pct=weather.humidity_pct,
        svf=vsi.svf,
        gvi=vsi.gvi,
        ground_albedo=ground_albedo,
        ground_emissivity=ground_emissivity,
        config=config.mrt,
    )

    # --- ③ 체감지수 (UTCI 우선 / PET) ---
    comfort = compute_comfort(
        tdb=weather.temperature_c,
        tr=mrt.tmrt,
        v=pwi.pedestrian_wind_speed_ms,
        rh=weather.humidity_pct,
        season=season,
        config=config.comfort,
    )

    risk = _classify_risk_thermal(comfort.value, comfort.index)

    return ThermalVPTIResult(
        vpti=comfort.value,
        comfort_index=comfort.index,
        risk_level=risk,
        stress_category=comfort.stress_category,
        season=season,
        base_temp=weather.temperature_c,
        pedestrian_wind_ms=pwi.pedestrian_wind_speed_ms,
        solar=solar,
        mrt=mrt,
        comfort=comfort,
        vsi=vsi,
        smti=smti,
        pwi=pwi,
    )


def compute_climate_index(
    views_5: list[ViewSegmentation],
    materials: list[MaterialFraction],
    weather: WeatherContext,
    road_axis_deg: float,
    *,
    lat: float | None = None,
    lon: float | None = None,
    when: datetime | None = None,
    sky_code: int | None = None,
    cloud_fraction: float | None = None,
    heading_deg: float = 0.0,
    solar_intensity: float = 0.0,
    ai_residual: float | None = None,
    ai_confidence: float | None = None,
    config: VPTICoreConfig = DEFAULT_CONFIG,
) -> VPTIResult | ThermalVPTIResult:
    """통합 디스패처 — config.vpti.integration_mode 로 경로 선택 (④).

    · "mrt_utci"(기본) → compute_vpti_thermal (lat/lon/when 필요)
    · "additive"       → compute_vpti (기존 가산형, 비교용)
    """
    if config.vpti.integration_mode == "mrt_utci":
        if lat is None or lon is None or when is None:
            raise ValueError("mrt_utci 모드는 lat, lon, when 인자가 필요함")
        return compute_vpti_thermal(
            views_5=views_5, materials=materials, weather=weather,
            road_axis_deg=road_axis_deg, lat=lat, lon=lon, when=when,
            sky_code=sky_code, cloud_fraction=cloud_fraction,
            heading_deg=heading_deg, ai_residual=ai_residual,
            ai_confidence=ai_confidence, config=config,
        )
    return compute_vpti(
        views_5=views_5, materials=materials, weather=weather,
        road_axis_deg=road_axis_deg, heading_deg=heading_deg,
        solar_intensity=solar_intensity, ai_residual=ai_residual,
        ai_confidence=ai_confidence, config=config,
    )
