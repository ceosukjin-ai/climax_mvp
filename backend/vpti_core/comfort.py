"""
체감지수 모듈 (③) — 기온·습도·보행자풍속(PWI)·MRT → UTCI(우선) 또는 PET.

체감지수 산출식은 ✅ 검증된 표준 라이브러리 pythermalcomfort 에 전적으로 위임한다
(임의 계수 0). 본 모듈은 입력 정규화·유효범위 처리·결과 래핑만 담당한다.

  · UTCI : Bröde et al.(2012) 6차 다항식 회귀 (ISB Commission 6 운영판).
           입력 = 기온 Tdb, 평균복사온도 Tr, 풍속 v(10m 기준), 상대습도 RH.
  · PET  : VDI 3787 Part 2 기반 정상상태 열수지 (Höppe MEMI 모델).

⚠️ 풍속 기준고도 주의: UTCI 표준 풍속은 10m 기준이나, 설계 요구사항(③)에 따라
  PWI 로 변환한 보행자 높이 풍속(≈1.5m)을 입력한다. 이는 "보행자가 실제로 느끼는
  바람"을 체감지수에 반영하려는 의도적 선택이며, 표준 정의(10m)와는 다르므로
  해석 시 유의한다. 유효범위(0.5~17 m/s) 밖이면 클램프.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pythermalcomfort.models import pet_steady, utci

from .config import ComfortConfig, DEFAULT_CONFIG, Season

ComfortIndex = Literal["utci", "pet"]


@dataclass(frozen=True, slots=True)
class ComfortResult:
    """체감지수 산출 결과."""

    index: ComfortIndex
    value: float                 # 체감온도 [°C] (UTCI 또는 PET)
    stress_category: str         # 표준 열스트레스 등급 (라이브러리 제공/도출)
    # 입력 echo (재현·디버깅용)
    tdb: float
    tr: float
    v: float                     # 실제 사용된(클램프된) 풍속
    v_input: float               # 클램프 전 입력 풍속
    rh: float
    clo: float | None = None     # PET 전용
    met: float | None = None     # PET 전용

    def as_dict(self) -> dict:
        d = {
            "index": self.index,
            "value": round(self.value, 2),
            "stress_category": self.stress_category,
            "inputs": {
                "tdb": round(self.tdb, 2),
                "tr": round(self.tr, 2),
                "v": round(self.v, 2),
                "v_input": round(self.v_input, 2),
                "rh": round(self.rh, 1),
            },
        }
        if self.clo is not None:
            d["inputs"]["clo"] = self.clo
            d["inputs"]["met"] = self.met
        return d


# PET 열스트레스 등급 — Matzarakis & Mayer (1996), 중부유럽 보정 표준 구간 [°C].
# (UTCI 는 라이브러리가 stress_category 를 직접 제공하므로 별도 표 불필요.)
_PET_CATEGORIES: tuple[tuple[float, str], ...] = (
    (4.0, "extreme cold stress"),
    (8.0, "strong cold stress"),
    (13.0, "moderate cold stress"),
    (18.0, "slight cold stress"),
    (23.0, "no thermal stress"),
    (29.0, "slight heat stress"),
    (35.0, "moderate heat stress"),
    (41.0, "strong heat stress"),
)


def _pet_category(pet: float) -> str:
    for upper, label in _PET_CATEGORIES:
        if pet < upper:
            return label
    return "extreme heat stress"


def _clo_for_season(season: Season | None, config: ComfortConfig) -> float:
    if season == "summer":
        return config.pet_clo_summer
    if season == "winter":
        return config.pet_clo_winter
    return config.pet_clo_transition


def compute_utci(
    tdb: float,
    tr: float,
    v: float,
    rh: float,
    config: ComfortConfig = DEFAULT_CONFIG.comfort,
) -> ComfortResult:
    """UTCI 산출 (pythermalcomfort, Bröde 2012)."""
    v_clamped = min(max(v, config.utci_wind_min), config.utci_wind_max)
    res = utci(tdb=tdb, tr=tr, v=v_clamped, rh=rh)
    return ComfortResult(
        index="utci",
        value=float(res.utci),
        stress_category=str(res.stress_category),
        tdb=tdb, tr=tr, v=v_clamped, v_input=v, rh=rh,
    )


def compute_pet(
    tdb: float,
    tr: float,
    v: float,
    rh: float,
    season: Season | None = None,
    config: ComfortConfig = DEFAULT_CONFIG.comfort,
) -> ComfortResult:
    """PET 산출 (pythermalcomfort.pet_steady, VDI 3787 / Höppe MEMI)."""
    clo = _clo_for_season(season, config)
    res = pet_steady(
        tdb=tdb, tr=tr, v=max(v, 0.1), rh=rh,
        met=config.pet_met, clo=clo, position=config.pet_position,
    )
    pet_value = float(res.pet)
    return ComfortResult(
        index="pet",
        value=pet_value,
        stress_category=_pet_category(pet_value),
        tdb=tdb, tr=tr, v=max(v, 0.1), v_input=v, rh=rh,
        clo=clo, met=config.pet_met,
    )


def compute_comfort(
    tdb: float,
    tr: float,
    v: float,
    rh: float,
    season: Season | None = None,
    config: ComfortConfig = DEFAULT_CONFIG.comfort,
) -> ComfortResult:
    """config.index 에 따라 UTCI(기본) 또는 PET 산출."""
    if config.index == "pet":
        return compute_pet(tdb, tr, v, rh, season, config)
    return compute_utci(tdb, tr, v, rh, config)
