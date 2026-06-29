"""
SMTI (Surface Material-Based Thermal Index) — 표면 재질 기반 열 지수.

특허: APE-2026-0656 (외부 환경 변화를 반영한 표면 재질 기반 열 지수 분석 방법)

각 표면 재질 i 에 대해 3개의 잠재력을 산출한 뒤(수학식 1~3), 가중 결합하여
재질별 열적 기여 함수 f_i 를 만들고(수학식 4), 공간 점유율로 가중 합산한다(수학식 5).

【수학식 1】 흡수 잠재력  A_i      = (1 − R_i) · I_i · (1 − σ_i)
【수학식 2】 축열 잠재력  S_i^store = C_i · (1 − σ_i)
【수학식 3】 방출 잠재력  E_i      = ε_i · (1 − σ_i)
【수학식 4】 열적 기여   f_i      = α·A_i + β·S_i^store + γ·E_i
【수학식 5】 SMTI       = Σ_{i=1}^N P_i · f_i
【수학식 6】 Σ_i P_i    = 1   (점유율 정규화)

기호
  R_i 반사율, I_i 일사량, σ_i 음영 계수(0=완전노출, 1=완전차폐),
  C_i 열용량, ε_i 방사율, P_i 공간 점유율,
  α 흡수 가중치, β 축열 가중치, γ 방출 가중치(음의 방향).

세 잠재력이 공통으로 (1 − σ_i) 를 포함 → 음영이 짙을수록 흡수·축열·방출이
동시에 감소하는 물리 현실을 반영(명세서 21쪽). α·β·γ 수치는 특허 미제시이므로
config.SMTIConfig 에서 ⚠️ UNCONFIRMED 로 관리한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_CONFIG, Season, SMTIConfig
from .materials import MaterialClass, get_properties


@dataclass(frozen=True, slots=True)
class MaterialFraction:
    """재질 i 의 공간 점유율 P_i (수학식 5)."""

    material: MaterialClass
    fraction: float  # P_i ∈ [0, 1]

    def __post_init__(self) -> None:
        if not 0.0 <= self.fraction <= 1.0:
            raise ValueError(
                f"fraction={self.fraction} out of [0, 1] (material={self.material})"
            )


@dataclass(frozen=True, slots=True)
class MaterialPotentials:
    """단일 재질의 잠재력 3종 분해 (수학식 1~3)."""

    material: str
    fraction: float       # P_i
    absorption: float     # A_i      (수학식 1)
    storage: float        # S_i^store (수학식 2)
    emission: float       # E_i      (수학식 3)
    contribution: float   # P_i · f_i (수학식 5의 항)


@dataclass(frozen=True, slots=True)
class SMTIResult:
    smti: float                                  # 수학식 5
    weights: tuple[float, float, float]          # (α, β, γ)
    solar_intensity: float                       # I (공통 일사량)
    shading_coefficient: float                   # σ (공통 음영 계수)
    per_material: tuple[MaterialPotentials, ...]  # 재질별 분해 (해석용)

    def as_dict(self) -> dict:
        return {
            "smti": round(self.smti, 4),
            "weights": {
                "alpha": self.weights[0],
                "beta": self.weights[1],
                "gamma": self.weights[2],
            },
            "solar_intensity": round(self.solar_intensity, 4),
            "shading_coefficient": round(self.shading_coefficient, 4),
            "per_material": [
                {
                    "material": m.material,
                    "fraction": round(m.fraction, 4),
                    "A_i": round(m.absorption, 4),
                    "S_i": round(m.storage, 4),
                    "E_i": round(m.emission, 4),
                    "contribution": round(m.contribution, 4),
                }
                for m in self.per_material
            ],
        }


def _normalize_fractions(
    materials: list[MaterialFraction],
) -> list[MaterialFraction]:
    """수학식 6 강제: Σ P_i = 1 이 되도록 정규화."""
    total = sum(m.fraction for m in materials)
    if total <= 0.0:
        raise ValueError("점유율 총합이 0 이하 — 정규화 불가")
    if abs(total - 1.0) < 1e-9:
        return materials
    return [MaterialFraction(m.material, m.fraction / total) for m in materials]


def compute_smti(
    materials: list[MaterialFraction],
    solar_intensity: float,
    shading_coefficient: float,
    season: Season | None = None,
    config: SMTIConfig = DEFAULT_CONFIG.smti,
) -> SMTIResult:
    """SMTI 산출 — 수학식 1~6을 원문 그대로 구현.

    Args:
        materials: 재질별 점유율 P_i 리스트 (총합이 1이 아니면 수학식 6으로 정규화).
        solar_intensity: 일사량 I. 본 참조 구현은 [0, 1] 정규화 일사를 받는다
            (재질·방향별 차등 일사가 필요하면 재질별 I_i로 확장 가능).
        shading_coefficient: 음영 계수 σ ∈ [0, 1]. 0=완전 노출, 1=완전 차폐.
        season: 계절. 주어지면 config의 계절별 (α, β, γ) 사용.
        config: SMTI 계수 설정.

    Returns:
        SMTIResult — 최종 SMTI 와 재질별 잠재력 분해.
    """
    if not materials:
        raise ValueError("materials 리스트가 비어 있음")
    if not 0.0 <= shading_coefficient <= 1.0:
        raise ValueError(f"shading={shading_coefficient} out of [0, 1]")
    if solar_intensity < 0.0:
        raise ValueError(f"solar_intensity={solar_intensity} must be ≥ 0")

    alpha, beta, gamma = config.weights_for(season)
    norm_materials = _normalize_fractions(materials)
    transmit = 1.0 - shading_coefficient  # (1 − σ) — 세 잠재력 공통항

    smti = 0.0
    per_material: list[MaterialPotentials] = []
    for m in norm_materials:
        props = get_properties(m.material)

        # 열용량 C_i 정규화 — 흡수/방출항(0~1)과 차원을 맞추기 위함 (⚠️ UNCONFIRMED).
        c_norm = props.heat_capacity / config.heat_capacity_reference

        a_i = (1.0 - props.reflectance) * solar_intensity * transmit  # 수학식 1
        s_i = c_norm * transmit                                        # 수학식 2
        e_i = props.emissivity * transmit                              # 수학식 3

        f_i = alpha * a_i + beta * s_i + gamma * e_i                   # 수학식 4
        contribution = m.fraction * f_i                                # 수학식 5의 항
        smti += contribution

        per_material.append(
            MaterialPotentials(
                material=m.material,
                fraction=m.fraction,
                absorption=a_i,
                storage=s_i,
                emission=e_i,
                contribution=contribution,
            )
        )

    return SMTIResult(
        smti=smti,
        weights=(alpha, beta, gamma),
        solar_intensity=solar_intensity,
        shading_coefficient=shading_coefficient,
        per_material=tuple(per_material),
    )


def shading_from_svf(svf: float) -> float:
    """SVF로부터 음영 계수 σ 근사 (σ = 1 − SVF).

    ⚠️ UNCONFIRMED — SMTI 특허는 σ를 "다방향 거리 영상 기반으로 산출"한다고만
    하고 SVF↔σ 관계식을 규정하지 않는다. 하늘이 많이 보일수록(SVF↑) 차폐가
    적다(σ↓)는 1차 근사. 정밀 모델은 태양 방위·건물 윤곽을 고려해야 함.
    """
    if not 0.0 <= svf <= 1.0:
        raise ValueError(f"svf={svf} out of [0, 1]")
    return 1.0 - svf
