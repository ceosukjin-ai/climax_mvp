"""
PHI — 생리 개인화 체감기후 엔진 (Personalized Heat-strain Index).

애플워치(HealthKit)에서 읽은 실시간 생체신호를 vpti_core 물리 기반 VPTI(PET 경로)에
반영하여 pVPTI(개인화 체감기후 지수)를 산출한다. 두 축으로 개인화한다:

  ① 대사율 M — activity(kcal/min) → PET met 입력.
     PET(compute_pet)는 met 를 상수(config.pet_met=1.37)로 고정하는데, 이를 실측
     대사율로 교체한다. 검증모델(VDI 3787 / Höppe MEMI)의 기존 입력을 실측값으로
     채우는 것이므로 임의 계수가 아닌 물리적 정공법이다.
  ② 잔차 심박부하 — HRR 원값은 운동만 해도 오르므로(선선한 날 빠른 걸음 = 허위경보),
     "그 활동량에서 기대되는 심박(%HRR≈%VO₂R)"을 빼고 운동으로 설명 안 되는 초과분
     (residual)만 위험경계에 반영한다. 활동량 자체는 이미 ①에서 PET 에 들어간다.
     activity 가 없으면 운동/더위를 가를 수 없으므로 strain=0(환경 PET 만 반영).

⚠️ ①은 표준(단위환산·검증모델)이나, 잔차→위험경계 결합계수(strain_shift_max)와
  VO₂max 기본값은 UNCONFIRMED — 실증 데이터(PHI 실증로깅)로 교정 대상.

관련: docs/PHI_HealthKit_통합계획.md, ios_handoff/HealthKitManager.swift
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from .comfort import ComfortResult, compute_pet
from .config import DEFAULT_CONFIG, PHIConfig, Season, VPTICoreConfig
from .vpti import (
    RiskLevel,
    ThermalVPTIResult,
    _classify_risk_thermal,
    compute_vpti_thermal,
)

Sex = Literal["male", "female"]


# =============================================================================
# 입력 — 애플워치 스냅샷 + 개인화 프로필
# =============================================================================
@dataclass(frozen=True, slots=True)
class Biometrics:
    """애플워치 → PHI 입력. Swift BiometricsSample 과 1:1 (hr, activity, hr_rest)."""

    hr: float | None = None        # 실시간 심박 [bpm]           ← heartRate
    activity: float | None = None  # 활동에너지 소비율 [kcal/min] ← activeEnergyBurned
    hr_rest: float | None = None   # 휴식심박 [bpm]              ← restingHeartRate
    hr_max: float | None = None    # 최대심박 [bpm] (없으면 콜드스타트)


@dataclass(frozen=True, slots=True)
class PhysiologyProfile:
    """개인화 파생값 — 프로필에서 최소 정보만(민감정보 최소화)."""

    age: int | None = None
    sex: Sex | None = None                 # hr_max 성별 분기 (여성 Gulati)
    height_cm: float | None = None
    weight_kg: float | None = None
    observed_hr_max: float | None = None   # 관측 최댓값(콜드스타트 보정)


@dataclass(frozen=True, slots=True)
class PersonalizedVPTIResult:
    """pVPTI 산출 결과. base_* 는 개인화 전 참조값(둘 다 PET, 동일 척도)."""

    pvpti: float                  # 개인화 체감기후 지수 [°C] (met 개인화 PET)
    base_vpti: float              # 개인화 전 PET [°C] (기본 met, 비교 기준)
    risk_level: RiskLevel         # 잔차 심박부하 반영 위험도
    base_risk_level: RiskLevel    # 개인화 전 위험도
    strain_index: float           # 잔차 심박부하 ∈ [0,1] (운동으로 설명 안 되는 초과분)
    observed_hrr: float | None    # 관측 HRR (참조/디버깅)
    expected_hrr: float | None    # 활동량 기대 HRR (참조/디버깅)
    metabolic_met: float | None   # 적용된 대사율 [met] (activity 없으면 None)
    hr_max_used: float | None     # 사용된 hr_max [bpm]
    season: Season                # 계절 (base 로부터)
    stress_category: str          # 개인화 PET 열스트레스 등급
    comfort: ComfortResult        # 개인화 PET 상세

    def as_dict(self) -> dict:
        return {
            "pvpti": round(self.pvpti, 2),
            "base_vpti": round(self.base_vpti, 2),
            "delta_personalization": round(self.pvpti - self.base_vpti, 2),
            "risk_level": self.risk_level,
            "base_risk_level": self.base_risk_level,
            "strain_index": round(self.strain_index, 3),
            "observed_hrr": (
                round(self.observed_hrr, 3) if self.observed_hrr is not None else None
            ),
            "expected_hrr": (
                round(self.expected_hrr, 3) if self.expected_hrr is not None else None
            ),
            "metabolic_met": (
                round(self.metabolic_met, 2) if self.metabolic_met is not None else None
            ),
            "hr_max_used": (
                round(self.hr_max_used, 1) if self.hr_max_used is not None else None
            ),
            "season": self.season,
            "stress_category": self.stress_category,
            "comfort": self.comfort.as_dict(),
        }


# =============================================================================
# ① 대사율 — activity(kcal/min) → met
# =============================================================================
def body_surface_area(
    height_cm: float | None, weight_kg: float | None, config: PHIConfig
) -> float:
    """DuBois 체표면적 A = 0.007184·H^0.725·W^0.425 [m²] (H cm, W kg).

    키·몸무게가 없으면 성인 근사 기본값. ✅ 표준식(Du Bois & Du Bois 1916).
    """
    if height_cm and weight_kg:
        return 0.007184 * (height_cm**0.725) * (weight_kg**0.425)
    return config.default_body_surface_area


def metabolic_rate_from_activity(
    activity_kcal_min: float, body_surface_area_m2: float, config: PHIConfig
) -> tuple[float, float]:
    """활동에너지 소비율 → (대사율 M[W/m²], met). ✅ 표준 단위환산.

    M[W] = kcal/min × 69.78,  M[W/m²] = M[W]/A,  met = M[W/m²]/58.15.
    """
    watts = activity_kcal_min * config.kcal_min_to_watt
    m_wm2 = watts / body_surface_area_m2
    met = m_wm2 / config.met_watt_per_m2
    return m_wm2, met


# =============================================================================
# ② 심박 — 성별 hr_max, 관측 HRR, 활동량 기대 HRR, 잔차
# =============================================================================
def estimate_hr_max(
    age: int | None,
    sex: Sex | None,
    observed_hr_max: float | None,
    config: PHIConfig,
) -> float | None:
    """hr_max 콜드스타트: 성별식 vs 관측최댓값 중 큰 값.

    ✅ 여성 Gulati et al.(2010) 206−0.88·age, 남성/기본 Tanaka et al.(2001) 208−0.7·age.
    HealthKit 에 hr_max 가 없으므로 연령·성별식으로 시작해 관측 최댓값으로 보정한다.
    """
    candidates: list[float] = []
    if age is not None:
        if sex == "female":
            candidates.append(
                config.hr_max_gulati_intercept - config.hr_max_gulati_slope * age
            )
        else:  # 남성 또는 미상 → Tanaka 기본
            candidates.append(
                config.hr_max_tanaka_intercept - config.hr_max_tanaka_slope * age
            )
    if observed_hr_max is not None:
        candidates.append(observed_hr_max)
    return max(candidates) if candidates else None


def heart_rate_reserve(
    hr: float | None, hr_rest: float | None, hr_max: float | None
) -> float | None:
    """관측 HRR = clamp((hr − hr_rest)/(hr_max − hr_rest), 0, 1). 입력 부족/역전 시 None."""
    if hr is None or hr_rest is None or hr_max is None:
        return None
    denom = hr_max - hr_rest
    if denom <= 0:
        return None
    return min(max((hr - hr_rest) / denom, 0.0), 1.0)


def expected_hrr_from_met(met: float, config: PHIConfig) -> float:
    """활동량에서 기대되는 %HRR. ✅ %HRR ≈ %VO₂R (ACSM/Swain 1997).

    expected = (met − 1)/(VO₂max_met − 1), clamp[0,1]. 안정(1 met)이면 0.
    """
    denom = config.vo2max_met - 1.0
    if denom <= 0:
        return 0.0
    return min(max((met - 1.0) / denom, 0.0), 1.0)


def residual_strain(observed_hrr: float, expected_hrr: float) -> float:
    """운동으로 설명 안 되는 심박 초과분 = clamp(observed − expected, 0, 1).

    선선한 날 빠른 걸음: observed↑ 지만 expected 도 같이↑ → 잔차≈0(허위경보 방지).
    더위 부하: 활동 대비 심박이 초과 → 잔차>0 → 위험경계 앞당김.
    """
    return min(max(observed_hrr - expected_hrr, 0.0), 1.0)


# =============================================================================
# 개인화 산출
# =============================================================================
def _pet_from(base: ThermalVPTIResult, met: float, config: VPTICoreConfig) -> ComfortResult:
    """base 의 물리 입력(Ta·Tmrt·u_p·RH)으로 지정 met 의 PET 재계산.

    UTCI 는 met 를 못 받으므로, base 가 UTCI 경로였더라도 개인화는 PET 로 환산한다
    (docs/연령개인화_적용계획.md 의 'PET 로 개인화' 원칙).
    """
    cfg = replace(config.comfort, index="pet", pet_met=met)
    return compute_pet(
        tdb=base.base_temp,
        tr=base.mrt.tmrt,
        v=base.pedestrian_wind_ms,
        rh=base.comfort.rh,
        season=base.season,
        config=cfg,
    )


def evaluate_personalized(
    base: ThermalVPTIResult,
    bio: Biometrics,
    profile: PhysiologyProfile | None = None,
    config: VPTICoreConfig = DEFAULT_CONFIG,
) -> PersonalizedVPTIResult:
    """물리 VPTI 결과 + 생체신호 → pVPTI.

    ① activity → met 로 PET 재계산(met 효과 순수 분리).
    ② 관측 HRR 에서 활동량 기대 HRR 을 뺀 잔차만 위험경계에 반영(pvpti 값은 물리 PET
       그대로 유지, 위험도 분류에만 strain_shift_max 만큼 앞당김).
       activity 없으면 운동/더위 분리 불가 → strain=0(환경 PET 만 반영).
    """
    phi = config.phi
    profile = profile or PhysiologyProfile()

    # 참조 PET(기본 met) — base 가 UTCI 였을 수 있으므로 동일 척도로 재계산
    base_pet = _pet_from(base, config.comfort.pet_met, config)

    # ① 대사율 개인화
    met: float | None = None
    if bio.activity is not None:
        bsa = body_surface_area(profile.height_cm, profile.weight_kg, phi)
        _, met = metabolic_rate_from_activity(bio.activity, bsa, phi)
        met = min(max(met, phi.met_min), phi.met_max)
        comfort = _pet_from(base, met, config)
    else:
        comfort = base_pet

    pvpti = comfort.value
    base_vpti = base_pet.value

    # ② 잔차 심박부하 — activity(활동 맥락) 있을 때만 산출, 없으면 억제(strain=0)
    hr_max = estimate_hr_max(
        profile.age, profile.sex, bio.hr_max or profile.observed_hr_max, phi
    )
    observed_hrr = heart_rate_reserve(bio.hr, bio.hr_rest, hr_max)
    expected_hrr: float | None = None
    strain = 0.0
    if met is not None and observed_hrr is not None:
        expected_hrr = expected_hrr_from_met(met, phi)
        strain = residual_strain(observed_hrr, expected_hrr)

    base_risk = _classify_risk_thermal(base_vpti, "pet")
    effective = pvpti + strain * phi.strain_shift_max   # ⚠️ UNCONFIRMED 결합
    risk = _classify_risk_thermal(effective, "pet")

    return PersonalizedVPTIResult(
        pvpti=pvpti,
        base_vpti=base_vpti,
        risk_level=risk,
        base_risk_level=base_risk,
        strain_index=strain,
        observed_hrr=observed_hrr,
        expected_hrr=expected_hrr,
        metabolic_met=met,
        hr_max_used=hr_max,
        season=base.season,
        stress_category=comfort.stress_category,
        comfort=comfort,
    )


def compute_pvpti(
    *,
    bio: Biometrics,
    profile: PhysiologyProfile | None = None,
    config: VPTICoreConfig = DEFAULT_CONFIG,
    **thermal_kwargs,
) -> PersonalizedVPTIResult:
    """편의 래퍼: compute_vpti_thermal 실행 → evaluate_personalized.

    thermal_kwargs 는 compute_vpti_thermal 인자(views_5, materials, weather,
    road_axis_deg, lat, lon, when, ...)를 그대로 전달한다.
    """
    base = compute_vpti_thermal(config=config, **thermal_kwargs)
    return evaluate_personalized(base, bio, profile, config)
