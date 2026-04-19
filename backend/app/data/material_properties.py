"""
도시 표면 재질별 열물성 데이터베이스.

SMTI 특허 명세서 7절 (실시예) 및 표준 열공학 참고값 기준.
각 재질의 반사율(albedo), 열용량, 방사율을 저장합니다.

출처:
- SMTI 특허 표 (2026.04.15)
- ASHRAE Handbook — Fundamentals
- Oke, T.R. (1987) Boundary Layer Climates
- Solar Energy, 2019, "Albedo of urban surfaces"

알베도: 태양복사 반사율 [0, 1]
열용량: MJ/(m³·K) — 단위 체적당 열저장 능력
방사율: 장파복사 방출률 [0, 1], 1에 가까울수록 흑체에 가까움
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MaterialClass = Literal[
    "asphalt",
    "concrete",
    "vegetation",
    "glass",
    "metal",
    "soil",
    "water",
    "brick",
    "stone",
    "wood",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class ThermalProperties:
    """재질의 열물성 3종.

    SMTI 특허 청구항 2: "열물성은 반사율(albedo), 열용량(heat capacity),
    방사율(emissivity) 중 하나 이상을 포함하는 것을 특징으로 하는 방법"
    """

    albedo: float  # 반사율 α ∈ [0, 1]
    heat_capacity: float  # 열용량 c_p × ρ, MJ/(m³·K)
    emissivity: float  # 방사율 ε ∈ [0, 1]

    def __post_init__(self) -> None:
        if not 0.0 <= self.albedo <= 1.0:
            raise ValueError(f"albedo={self.albedo} out of [0, 1]")
        if self.heat_capacity <= 0.0:
            raise ValueError(f"heat_capacity={self.heat_capacity} must be positive")
        if not 0.0 <= self.emissivity <= 1.0:
            raise ValueError(f"emissivity={self.emissivity} out of [0, 1]")


MATERIAL_DB: dict[MaterialClass, ThermalProperties] = {
    # SMTI 특허 실시예 표 (2026.04.15) 기준값
    "asphalt": ThermalProperties(
        albedo=0.05,
        heat_capacity=2.09,
        emissivity=0.95,
    ),
    "concrete": ThermalProperties(
        albedo=0.30,
        heat_capacity=0.88,
        emissivity=0.92,
    ),
    "vegetation": ThermalProperties(
        albedo=0.20,
        heat_capacity=4.18,
        emissivity=0.98,
    ),
    "glass": ThermalProperties(
        albedo=0.10,
        heat_capacity=0.84,
        emissivity=0.84,
    ),
    # 확장 재질 (특허 청구항 9 대응 — 아스팔트, 콘크리트, 식생, 유리, 금속, 토양)
    "metal": ThermalProperties(
        albedo=0.55,
        heat_capacity=3.50,  # 스틸 기준, 표면 마감 따라 큰 편차
        emissivity=0.25,  # 광택 금속은 매우 낮음
    ),
    "soil": ThermalProperties(
        albedo=0.17,
        heat_capacity=1.42,  # 건조 토양
        emissivity=0.94,
    ),
    "water": ThermalProperties(
        albedo=0.06,
        heat_capacity=4.18,
        emissivity=0.96,
    ),
    "brick": ThermalProperties(
        albedo=0.30,
        heat_capacity=1.37,
        emissivity=0.93,
    ),
    "stone": ThermalProperties(
        albedo=0.22,
        heat_capacity=2.30,
        emissivity=0.92,
    ),
    "wood": ThermalProperties(
        albedo=0.35,
        heat_capacity=1.20,
        emissivity=0.90,
    ),
    # 미분류 — 콘크리트 평균값 사용 (도시 환경 기본값)
    "unknown": ThermalProperties(
        albedo=0.25,
        heat_capacity=1.50,
        emissivity=0.90,
    ),
}


def get_properties(material: MaterialClass) -> ThermalProperties:
    """재질명으로 열물성 조회. 미등록 재질은 unknown 대체."""
    return MATERIAL_DB.get(material, MATERIAL_DB["unknown"])
