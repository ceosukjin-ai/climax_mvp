"""
재질별 열물성 데이터베이스 — SMTI 수학식 1~3의 입력값.

SMTI 특허(APE-2026-0656)는 "재질별 열물성 라이브러리"로부터 반사율 R,
열용량 C, 방사율 ε 을 조회한다고 규정한다(명세서 16~17쪽). 본 모듈이 그
라이브러리에 해당한다.

특허 명시 예시 (명세서 16쪽, 아스팔트):
  - 흡수(absorptivity = 1−R) ≈ 0.85 ~ 0.95  →  R ≈ 0.05 ~ 0.15
  - 축열(specific heat)      ≈ 920 J/kg·K
  - 방출(emissivity ε)       ≈ 0.90 ~ 0.98

위 예시 외 재질의 구체 수치는 특허에 표로 제시되지 않으므로 ⚠️ UNCONFIRMED.
표준 열공학 문헌값(ASHRAE Handbook, Oke 1987 Boundary Layer Climates)을
사용하며, 기존 app.data.material_properties 와 동일한 출처·값을 따른다.

단위
  reflectance R : 태양복사 반사율 [0, 1]              (= albedo)
  heat_capacity C : 단위 체적당 열용량 MJ/(m³·K)       (= ρ·c_p)
  emissivity ε  : 장파복사 방사율 [0, 1]
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
    """SMTI 수학식 1~3 입력 — 반사율 R, 열용량 C, 방사율 ε."""

    reflectance: float       # R_i  : 반사율 [0, 1]
    heat_capacity: float     # C_i  : 열용량 MJ/(m³·K), 양수
    emissivity: float        # ε_i  : 방사율 [0, 1]

    def __post_init__(self) -> None:
        if not 0.0 <= self.reflectance <= 1.0:
            raise ValueError(f"reflectance={self.reflectance} out of [0, 1]")
        if self.heat_capacity <= 0.0:
            raise ValueError(f"heat_capacity={self.heat_capacity} must be positive")
        if not 0.0 <= self.emissivity <= 1.0:
            raise ValueError(f"emissivity={self.emissivity} out of [0, 1]")


# ⚠️ 아스팔트만 특허 예시와 정합(R≈0.05, ε≈0.95). 나머지는 UNCONFIRMED 문헌값.
MATERIAL_DB: dict[MaterialClass, ThermalProperties] = {
    # ✅ 아스팔트: 특허 명세서 16쪽 예시 범위와 일치 (R≈0.05, ε≈0.95)
    "asphalt": ThermalProperties(reflectance=0.05, heat_capacity=2.09, emissivity=0.95),
    "concrete": ThermalProperties(reflectance=0.30, heat_capacity=0.88, emissivity=0.92),
    "vegetation": ThermalProperties(reflectance=0.20, heat_capacity=4.18, emissivity=0.98),
    "glass": ThermalProperties(reflectance=0.10, heat_capacity=0.84, emissivity=0.84),
    "metal": ThermalProperties(reflectance=0.55, heat_capacity=3.50, emissivity=0.25),
    "soil": ThermalProperties(reflectance=0.17, heat_capacity=1.42, emissivity=0.94),
    "water": ThermalProperties(reflectance=0.06, heat_capacity=4.18, emissivity=0.96),
    "brick": ThermalProperties(reflectance=0.30, heat_capacity=1.37, emissivity=0.93),
    "stone": ThermalProperties(reflectance=0.22, heat_capacity=2.30, emissivity=0.92),
    "wood": ThermalProperties(reflectance=0.35, heat_capacity=1.20, emissivity=0.90),
    # 미분류 — 도시 환경 평균값(콘크리트 근사)으로 폴백
    "unknown": ThermalProperties(reflectance=0.25, heat_capacity=1.50, emissivity=0.90),
}


def get_properties(material: MaterialClass) -> ThermalProperties:
    """재질명으로 열물성 조회. 미등록 재질은 'unknown'으로 폴백."""
    return MATERIAL_DB.get(material, MATERIAL_DB["unknown"])
