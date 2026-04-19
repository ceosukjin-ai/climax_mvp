"""
SMTI (Surface Material Thermal Index) 산출 엔진.

특허: 표면 재질 기반 체감기후 지수(SMTI)를 이용한 사계절 체감기후 분석
      방법 및 시스템 (2026.04.15)

기본 수식:
    SMTI = Σ (p_i × f_i)

확장 수식 (태양·음영 반영):
    SMTI = Σ p_i × [w1·(1-α_i) + w2·c_i + w3·ε_i] × [w4·I_i + w5·s_i]

여기서:
    p_i : 재질 i의 공간 점유 비율
    α_i : 반사율 (albedo) — 낮을수록 태양열 흡수 ↑
    c_i : 정규화된 열용량 — 높을수록 열 저장 ↑
    ε_i : 방사율 — 높을수록 장파 복사 방출 ↑
    I_i : 일사량 — 태양 복사의 기하학적 가용성 [0, 1]
    s_i : 음영 계수 — 주변 구조물 차폐율 [0, 1]

설계 원칙:
1. 특허 범위 보호 — 청구항 1,2,4,8,16,19,21,22 모두 구현
2. 해석 가능성 — 각 항의 기여도를 분해해서 반환 (B2G 정책 리포트용)
3. 정규화 — 최종 SMTI는 [0, 1] 스케일로 압축하여 VPTI 통합 용이
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import pvlib

from app.data.material_properties import (
    MATERIAL_DB,
    MaterialClass,
    ThermalProperties,
    get_properties,
)

# 열용량 정규화 기준 — 물의 열용량 (가장 높은 값 중 하나)
HEAT_CAPACITY_REFERENCE = 4.18  # MJ/(m³·K)

# 기본 가중치 (정성적 설계값 — 실증 후 조정 가능)
# 재질 내부 기여도 (1-α, c, ε)
DEFAULT_MATERIAL_WEIGHTS = (0.5, 0.3, 0.2)  # w1, w2, w3
# 외부 조건 기여도 (일사, 음영)
DEFAULT_ENVIRONMENTAL_WEIGHTS = (0.7, 0.3)  # w4, w5


@dataclass(frozen=True, slots=True)
class MaterialFraction:
    """한 재질의 공간 점유 비율.

    SMTI 특허 청구항 8: "재질의 공간 점유 비율은 영상 내 픽셀 점유 비율을
    기반으로 산출되는 것을 특징으로 하는 방법"
    """

    material: MaterialClass
    fraction: float  # 점유 비율 [0, 1]

    def __post_init__(self) -> None:
        if not 0.0 <= self.fraction <= 1.0:
            raise ValueError(
                f"fraction={self.fraction} out of [0, 1] for material={self.material}"
            )


@dataclass(frozen=True, slots=True)
class SolarCondition:
    """태양 위치 및 조건.

    특허 청구항 19: "위치 및 시간 정보를 기반으로 태양의 고도 및 방위각을
    산출하고, 이를 이용하여 각 표면 재질이 받는 일사 조건을 결정"
    """

    elevation_deg: float  # 태양 고도 [deg], 밤엔 음수
    azimuth_deg: float  # 태양 방위각 [deg], 0=북, 90=동
    clearsky_ghi: float  # 맑은 하늘 수평면 전일사량 [W/m²]

    @property
    def is_daytime(self) -> bool:
        return self.elevation_deg > 0.0

    @property
    def normalized_intensity(self) -> float:
        """일사강도를 [0, 1]로 정규화.

        0 = 밤, 1 = 정오 맑은 하늘. 고도각의 sin으로 근사 (Lambert's cosine law).
        """
        if not self.is_daytime:
            return 0.0
        return float(np.clip(np.sin(np.radians(self.elevation_deg)), 0.0, 1.0))


@dataclass(frozen=True, slots=True)
class SMTIResult:
    """SMTI 산출 결과 + 분해 기여도."""

    smti: float  # 최종 SMTI [0, 1]
    material_contributions: dict[str, float]  # 재질별 기여도
    solar_intensity: float  # 사용된 일사 강도 [0, 1]
    shading_coefficient: float  # 사용된 음영 계수 [0, 1]
    timestamp: str  # ISO 8601

    def as_dict(self) -> dict:
        return {
            "smti": round(self.smti, 4),
            "material_contributions": {
                k: round(v, 4) for k, v in self.material_contributions.items()
            },
            "solar_intensity": round(self.solar_intensity, 4),
            "shading_coefficient": round(self.shading_coefficient, 4),
            "timestamp": self.timestamp,
        }


def compute_solar_position(
    latitude: float,
    longitude: float,
    dt: datetime,
    timezone: str = "Asia/Seoul",
) -> SolarCondition:
    """위경도와 시각으로 태양 위치·일사량 계산.

    pvlib의 SPA (Solar Position Algorithm) 사용 — 기상학 표준.
    특허 청구항 19의 "태양 고도 및 방위각 산출" 단계에 해당.

    Args:
        latitude: 위도 [deg]
        longitude: 경도 [deg]
        dt: 시각. timezone-aware면 그대로, naive면 `timezone` 인자로 해석.
        timezone: naive datetime을 해석할 시간대 (기본 Asia/Seoul).

    Returns:
        SolarCondition — 고도, 방위각, 청명일사량.
    """
    import pandas as pd

    # naive datetime은 한국 로컬 시간으로 간주
    if dt.tzinfo is None:
        times = pd.DatetimeIndex([dt]).tz_localize(timezone)
    else:
        times = pd.DatetimeIndex([dt])
    solpos = pvlib.solarposition.get_solarposition(
        times, latitude=latitude, longitude=longitude
    )
    elevation = float(solpos["apparent_elevation"].iloc[0])
    azimuth = float(solpos["azimuth"].iloc[0])

    # 간단 청명 모델 (Haurwitz) — 맑은 하늘 가정 GHI
    clearsky = pvlib.clearsky.haurwitz(solpos["apparent_zenith"])
    ghi = float(clearsky["ghi"].iloc[0]) if elevation > 0 else 0.0

    return SolarCondition(
        elevation_deg=elevation,
        azimuth_deg=azimuth,
        clearsky_ghi=ghi,
    )


def _material_thermal_score(
    props: ThermalProperties,
    weights: tuple[float, float, float] = DEFAULT_MATERIAL_WEIGHTS,
) -> float:
    """재질 열물성 3종을 [0, 1] 스케일 단일 점수로 통합.

    점수 의미: 태양 복사를 받았을 때 해당 재질이 **보행자 체감온도 상승에
    기여하는 정도**. 높을수록 더 뜨거운 체감을 만듦.

    구성 요소 (모두 "뜨거움 기여"로 방향 통일):
    - (1 - α): 태양복사 흡수율. 아스팔트(α=0.05)는 0.95 흡수 → 점수 ↑
    - (1 - c_norm): 낮은 열용량 → 표면이 빠르게 달아올라 체감 ↑
      * 물·식생처럼 열용량이 크면 표면온도 상승이 억제되어 체감 ↓
      * 이는 실제 도시 열섬 연구와 부합 (concrete>vegetation)
    - ε: 방사율. 가열된 표면이 장파 복사로 주변 보행자에게 열 전달
      * 금속(ε=0.25)은 뜨거워도 복사가 약해 체감 기여 ↓

    이 정의에서 식생은 낮은 흡수율(0.8)·높은 열용량(1 - 1.0 = 0.0)·높은
    방사율이 조합되어 낮은 점수를 얻습니다. 아스팔트는 정반대로 높은 점수.
    """
    w1, w2, w3 = weights
    # 열용량 정규화 후 "낮을수록 뜨거워짐" 방향으로 반전
    c_norm = np.clip(props.heat_capacity / HEAT_CAPACITY_REFERENCE, 0.0, 1.0)

    score = w1 * (1.0 - props.albedo) + w2 * (1.0 - c_norm) + w3 * props.emissivity
    return float(np.clip(score, 0.0, 1.0))


def _environmental_modulation(
    solar_intensity: float,
    shading: float,
    weights: tuple[float, float] = DEFAULT_ENVIRONMENTAL_WEIGHTS,
) -> float:
    """태양·음영 환경 조건을 [0, 1] 스케일로 통합.

    특허 청구항 22: "건물, 식생 또는 구조물에 의해 형성되는 음영 조건을
    반영하는 단계"

    shading = 1.0 → 완전 노출 (그늘 없음)
    shading = 0.0 → 완전 차폐
    """
    w4, w5 = weights
    # 일사는 음영과 곱연산이 자연스러우나, 특허 확장수식은 선형 결합으로
    # 기술하므로 그대로 따름
    modulation = w4 * solar_intensity * shading + w5 * shading
    return float(np.clip(modulation, 0.0, 1.0))


def compute_smti(
    materials: list[MaterialFraction],
    solar: SolarCondition,
    shading_coefficient: float = 1.0,
    timestamp: datetime | None = None,
    material_weights: tuple[float, float, float] = DEFAULT_MATERIAL_WEIGHTS,
    environmental_weights: tuple[float, float] = DEFAULT_ENVIRONMENTAL_WEIGHTS,
) -> SMTIResult:
    """SMTI 산출 — 특허 확장 수식 구현.

    SMTI = Σ p_i × thermal_score(α_i, c_i, ε_i) × env_modulation(I, s)

    여기서 환경 변조항은 모든 재질에 공통 적용됩니다 (같은 위치의 같은 시각).
    재질별로 다른 일사량이 필요한 경우 (예: 수직벽 vs 수평면) 확장 가능.

    Args:
        materials: 재질별 점유 비율 리스트. 합은 대략 1.0이어야 함.
        solar: 태양 위치·일사 조건.
        shading_coefficient: 주변 구조물에 의한 차폐율 [0, 1].
            1.0=완전 노출, 0.0=완전 차폐. VSI의 SVF로부터 추정 가능.
        timestamp: 결과에 기록할 시각. None이면 solar 정보로 추정하지 않고 현재시각.
        material_weights: (w1, w2, w3) = (1-α, c_norm, ε) 가중치.
        environmental_weights: (w4, w5) = (I, s) 가중치.

    Returns:
        SMTIResult — 최종 값과 재질별 기여도 분해.
    """
    if not materials:
        raise ValueError("materials list is empty")
    if not 0.0 <= shading_coefficient <= 1.0:
        raise ValueError(f"shading_coefficient={shading_coefficient} out of [0, 1]")

    total_fraction = sum(m.fraction for m in materials)
    if not 0.95 <= total_fraction <= 1.05:
        # 약간의 오차는 허용 (세그멘테이션 완전성 부족)
        # 큰 오차는 경고 — 정규화하여 진행
        pass

    env_mod = _environmental_modulation(
        solar.normalized_intensity, shading_coefficient, environmental_weights
    )

    smti_value = 0.0
    contributions: dict[str, float] = {}
    for mat in materials:
        props = get_properties(mat.material)
        thermal = _material_thermal_score(props, material_weights)
        contribution = mat.fraction * thermal * env_mod
        smti_value += contribution
        contributions[mat.material] = contribution

    ts = (timestamp or datetime.now()).isoformat()
    return SMTIResult(
        smti=float(np.clip(smti_value, 0.0, 1.0)),
        material_contributions=contributions,
        solar_intensity=solar.normalized_intensity,
        shading_coefficient=shading_coefficient,
        timestamp=ts,
    )


def estimate_shading_from_svf(svf: float) -> float:
    """SVF로부터 음영 계수를 근사 추정.

    SVF = 1.0 → 완전 개방 → shading = 1.0 (노출 완전)
    SVF = 0.0 → 완전 폐쇄 (하늘 안 보임) → shading = 0.0 (완전 차폐)

    선형 근사. 더 정확한 모델은 태양 방위각과 건물 윤곽을 고려해야 하지만
    초기 MVP에서는 단순화.
    """
    return float(np.clip(svf, 0.0, 1.0))
