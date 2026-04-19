"""
PWI (Pedestrian Wind Intelligence) 산출 엔진.

사업계획서 1.3절: "보행자 높이 기준 풍환경 (체감 바람) 분석"

PWI는 기상청 관측소 풍속(통상 10m 높이)을 보행자 높이(1.5m)로
downscaling 하고, 도시 공간 구조(SVF, BVI)에 의한 감쇠를 반영하여
실제 보행자가 체감하는 바람 강도를 추정합니다.

체감 관점:
- 강풍은 여름에 쾌적 (냉각), 겨울에 불쾌 (체감 한파)
- 고층 빌딩 사이 venturi 효과로 국소 가속 가능
- 녹지는 바람을 약하게 함

기본 모델:
    u(z=1.5m) = u(z=10m) × (1.5/10)^α × F_urban

여기서:
    α : 로그/지수 프로파일 지수 (도시 지표 거칠기 → 0.25 ~ 0.35)
    F_urban : 도시 형태 기반 보정 계수 ∈ [0, 1]
              SVF가 작고 BVI가 크면 감쇠 ↑ (건물에 가려짐)
              단, 특정 방향·배치에서는 증폭 가능 (여기선 보수적으로 감쇠만)

이 모듈은 물리 모델로 구현하며, 향후 실측 데이터로 회귀 학습된
가중치를 대체 주입 가능하도록 파라미터화되어 있습니다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

# 도시 지표의 대표적 지수 프로파일 지수 (ASCE 7, Class B 도시)
DEFAULT_PROFILE_EXPONENT = 0.30

# 관측 고도 (기상청 표준)
REFERENCE_HEIGHT_M = 10.0

# 보행자 높이
PEDESTRIAN_HEIGHT_M = 1.5


@dataclass(frozen=True, slots=True)
class WindCondition:
    """기상 관측소 기반 바람 조건 (10m 고도)."""

    speed_ms: float  # 풍속 [m/s]
    direction_deg: float  # 풍향 [deg], 0=북, 90=동 (기상학 convention)
    temperature_c: float  # 기온 [°C] — 체감 바람 평가용

    def __post_init__(self) -> None:
        if self.speed_ms < 0.0:
            raise ValueError(f"speed_ms={self.speed_ms} must be non-negative")
        if not 0.0 <= self.direction_deg < 360.0:
            raise ValueError(f"direction_deg={self.direction_deg} out of [0, 360)")


@dataclass(frozen=True, slots=True)
class PWIResult:
    """PWI 산출 결과."""

    pedestrian_wind_speed_ms: float  # 보행자 높이 추정 풍속 [m/s]
    pwi: float  # PWI 정규화 값 [0, 1]
    profile_reduction: float  # 고도 프로파일로 인한 감소 계수
    urban_reduction: float  # 도시 형태로 인한 감소 계수
    wind_chill_severity: Literal["calm", "mild", "strong", "hazardous"]

    def as_dict(self) -> dict:
        return {
            "pedestrian_wind_speed_ms": round(self.pedestrian_wind_speed_ms, 3),
            "pwi": round(self.pwi, 4),
            "profile_reduction": round(self.profile_reduction, 4),
            "urban_reduction": round(self.urban_reduction, 4),
            "wind_chill_severity": self.wind_chill_severity,
        }


def downscale_to_pedestrian_height(
    wind_speed_10m: float,
    exponent: float = DEFAULT_PROFILE_EXPONENT,
) -> tuple[float, float]:
    """10m 풍속을 1.5m 보행자 높이로 변환 (power law).

    u(z) / u(z_ref) = (z / z_ref)^α

    Args:
        wind_speed_10m: 기상청 관측 풍속 [m/s] @ 10m.
        exponent: 지수 프로파일 α. 도시 ~0.30.

    Returns:
        (pedestrian_speed, reduction_factor) 튜플.
    """
    if wind_speed_10m < 0.0:
        raise ValueError(f"wind_speed_10m={wind_speed_10m} must be non-negative")

    ratio = (PEDESTRIAN_HEIGHT_M / REFERENCE_HEIGHT_M) ** exponent
    return wind_speed_10m * ratio, ratio


def urban_form_reduction(svf: float, bvi: float) -> float:
    """공간 구조 기반 바람 감쇠 계수 [0, 1].

    SVF가 낮고 BVI가 높을수록 바람이 건물에 가려져 약해진다고 가정.
    논리적 모델 (실측 검증 전):
        F = SVF^a × (1 - BVI)^b

    현재 경험적 a=0.5, b=0.3 사용. 실증 데이터로 회귀 학습 후 대체.

    Args:
        svf: Sky View Factor [0, 1]
        bvi: Building View Index [0, 1]

    Returns:
        감쇠 계수 [0, 1]. 1에 가까울수록 바람이 잘 통함.
    """
    if not 0.0 <= svf <= 1.0 or not 0.0 <= bvi <= 1.0:
        raise ValueError(f"svf={svf}, bvi={bvi} must be in [0, 1]")

    # svf^0.5: 개방감의 제곱근 (완전 폐쇄가 아니면 바람은 꽤 통함)
    # (1-bvi)^0.3: 건물 장애물의 완화된 감쇠
    sky_factor = np.sqrt(max(svf, 1e-6))
    building_factor = (1.0 - bvi) ** 0.3

    return float(np.clip(sky_factor * building_factor, 0.0, 1.0))


def _normalize_pwi(pedestrian_speed_ms: float) -> float:
    """보행자 풍속을 [0, 1] PWI로 정규화.

    Beaufort 척도 기반:
    - 0 m/s = 완전 무풍 (PWI = 0)
    - 1.5 m/s = 연풍 (PWI ≈ 0.15)
    - 5 m/s = 상쾌한 바람 (PWI ≈ 0.5)
    - 10 m/s = 강풍 (PWI ≈ 1.0)
    - 15+ m/s = 포화 (PWI = 1.0)

    Sigmoid 유사 곡선 사용.
    """
    reference_strong = 10.0  # m/s
    normalized = pedestrian_speed_ms / reference_strong
    # soft saturation via tanh
    return float(np.clip(np.tanh(normalized), 0.0, 1.0))


def _classify_wind_chill(
    pedestrian_speed: float, temperature_c: float
) -> Literal["calm", "mild", "strong", "hazardous"]:
    """체감 바람 단계 분류.

    단순 분류 — 정밀 wind chill 공식은 VPTI에서 별도 처리.
    """
    if pedestrian_speed < 1.5:
        return "calm"
    if pedestrian_speed < 5.0:
        return "mild"
    # 저온에서 강풍은 위험도 상승
    if temperature_c < 5.0 and pedestrian_speed >= 5.0:
        return "hazardous"
    if pedestrian_speed < 10.0:
        return "strong"
    return "hazardous"


def compute_pwi(
    wind: WindCondition,
    svf: float,
    bvi: float,
    profile_exponent: float = DEFAULT_PROFILE_EXPONENT,
) -> PWIResult:
    """PWI 산출 — 기상 풍속 + 공간 구조 → 보행자 체감 풍환경.

    Args:
        wind: 기상청 관측 풍속·풍향·기온.
        svf: Sky View Factor [0, 1]. VSI 결과에서 받음.
        bvi: Building View Index [0, 1]. VSI 결과에서 받음.
        profile_exponent: 고도 프로파일 지수. 기본 0.30 (도시).

    Returns:
        PWIResult — 보행자 풍속 추정치 + 정규화 PWI + 분류.
    """
    ped_speed_profile, profile_reduction = downscale_to_pedestrian_height(
        wind.speed_ms, profile_exponent
    )
    urban_reduction = urban_form_reduction(svf, bvi)

    final_speed = ped_speed_profile * urban_reduction
    pwi_norm = _normalize_pwi(final_speed)

    return PWIResult(
        pedestrian_wind_speed_ms=final_speed,
        pwi=pwi_norm,
        profile_reduction=profile_reduction,
        urban_reduction=urban_reduction,
        wind_chill_severity=_classify_wind_chill(final_speed, wind.temperature_c),
    )
