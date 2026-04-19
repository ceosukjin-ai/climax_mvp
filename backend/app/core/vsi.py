"""
VSI (Visual Spatial Index) 산출 엔진.

논문: Visual Spatial Index, A Multi-Directional Street-View-Based Indicator
      for Pedestrian Thermal Environment
특허: 다방향 거리영상 기반 시각환경 지수(VSI) 산출 방법 및 시스템

VSI = w_svf * SVF + w_gvi * (1 - GVI) + w_bvi * BVI

- SVF (Sky View Factor): 상향 시야 영상의 하늘 영역 비율 [0, 1]
- GVI (Green View Index): 수평 4방향 식생 영역 평균 비율 [0, 1]
- BVI (Building View Index): 수평 4방향 건물 영역 평균 비율 [0, 1]

가중치 기본값은 논문 검증값 (0.5, 0.3, 0.2).
지역·계절별 학습된 가중치로 덮어쓸 수 있도록 파라미터화됨.

특허 청구항 5 "선형 결합" 범위 내 구현이므로 권리범위 안전.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

ViewDirection = Literal["front", "back", "left", "right", "up"]
REQUIRED_HORIZONTAL_VIEWS: tuple[ViewDirection, ...] = ("front", "back", "left", "right")
REQUIRED_ALL_VIEWS: tuple[ViewDirection, ...] = ("front", "back", "left", "right", "up")

# 논문 검증 가중치 (PNU 캠퍼스 실측 기준 R²=0.222)
DEFAULT_WEIGHTS = (0.5, 0.3, 0.2)

# 특허 명세서 대표 실시예 가중치 (참고용)
PATENT_EXAMPLE_WEIGHTS = (1.0, 1.0, 1.0)

# 논문 Table 3 임계값 (기본 가중치 기준)
VSI_THRESHOLD_LOW = 0.56
VSI_THRESHOLD_HIGH = 0.71


@dataclass(frozen=True, slots=True)
class ViewSegmentation:
    """단일 시야 방향의 세그멘테이션 결과.

    각 클래스 픽셀 비율을 저장합니다. 합은 대략 1.0이지만
    ground 등 기타 클래스가 있어 정확히 1이 아닐 수 있습니다.
    """

    direction: ViewDirection
    sky_ratio: float  # 하늘 픽셀 비율 [0, 1]
    vegetation_ratio: float  # 식생 픽셀 비율 [0, 1]
    building_ratio: float  # 건물 픽셀 비율 [0, 1]
    ground_ratio: float = 0.0  # 지면 픽셀 비율 [0, 1]

    def __post_init__(self) -> None:
        for name, value in (
            ("sky_ratio", self.sky_ratio),
            ("vegetation_ratio", self.vegetation_ratio),
            ("building_ratio", self.building_ratio),
            ("ground_ratio", self.ground_ratio),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{name}={value} must be in [0, 1] for direction={self.direction}"
                )


@dataclass(frozen=True, slots=True)
class VSIResult:
    """VSI 산출 결과 + 중간 지표."""

    svf: float  # Sky View Factor
    gvi: float  # Green View Index
    bvi: float  # Building View Index
    vsi: float  # 최종 VSI
    weights: tuple[float, float, float]  # 사용된 가중치
    category: Literal["Low", "Moderate", "High"]  # 논문 임계값 기반 분류

    def as_dict(self) -> dict[str, float | str | tuple[float, ...]]:
        return {
            "svf": round(self.svf, 4),
            "gvi": round(self.gvi, 4),
            "bvi": round(self.bvi, 4),
            "vsi": round(self.vsi, 4),
            "weights": self.weights,
            "category": self.category,
        }


def _classify_vsi(vsi: float) -> Literal["Low", "Moderate", "High"]:
    """논문 Table 3 임계값 기반 분류.

    Low < 0.56, Moderate 0.56 ~ 0.71, High > 0.71
    기본 가중치 (0.5, 0.3, 0.2) 기준. 다른 가중치 사용 시
    해당 가중치로 재보정된 임계값을 써야 함.
    """
    if vsi < VSI_THRESHOLD_LOW:
        return "Low"
    if vsi <= VSI_THRESHOLD_HIGH:
        return "Moderate"
    return "High"


def compute_svf(up_view: ViewSegmentation) -> float:
    """상향 시야 영상에서 SVF 산출.

    특허 도면 2: "상향 시야 영상으로부터는 하늘 영역의 비율을 이용하여
    Sky View Factor(SVF)를 산출"

    Args:
        up_view: direction='up'인 시야 세그멘테이션.

    Returns:
        SVF 값 [0, 1].
    """
    if up_view.direction != "up":
        raise ValueError(f"SVF requires up view, got {up_view.direction}")
    return up_view.sky_ratio


def compute_gvi(horizontal_views: list[ViewSegmentation]) -> float:
    """수평 4방향 시야 평균에서 GVI 산출.

    특허 도면 2: "수평 방향의 다중 시야 영상으로부터는 식생 및 건물 영역의
    비율을 이용하여 Green View Index(GVI) ... 를 산출"

    논문 2.2절: "the Green View Index (GVI) and Building View Index (BVI) were
    computed as the average ratios of vegetation and building pixels across
    the four horizontal views, respectively"

    Args:
        horizontal_views: front, back, left, right 4개 시야.

    Returns:
        GVI 값 [0, 1] — 4방향 식생비율 평균.
    """
    _validate_horizontal_views(horizontal_views)
    ratios = [v.vegetation_ratio for v in horizontal_views]
    return float(np.mean(ratios))


def compute_bvi(horizontal_views: list[ViewSegmentation]) -> float:
    """수평 4방향 시야 평균에서 BVI 산출.

    Args:
        horizontal_views: front, back, left, right 4개 시야.

    Returns:
        BVI 값 [0, 1] — 4방향 건물비율 평균.
    """
    _validate_horizontal_views(horizontal_views)
    ratios = [v.building_ratio for v in horizontal_views]
    return float(np.mean(ratios))


def _validate_horizontal_views(views: list[ViewSegmentation]) -> None:
    """수평 4방향이 정확히 한 번씩 들어있는지 확인."""
    if len(views) != 4:
        raise ValueError(f"Expected 4 horizontal views, got {len(views)}")
    directions = {v.direction for v in views}
    required = set(REQUIRED_HORIZONTAL_VIEWS)
    if directions != required:
        missing = required - directions
        extra = directions - required
        raise ValueError(
            f"Horizontal views mismatch. missing={missing}, extra={extra}"
        )


def compute_vsi(
    views_5: list[ViewSegmentation],
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
) -> VSIResult:
    """전체 VSI 파이프라인 실행.

    5-view 세그멘테이션 결과를 받아 SVF, GVI, BVI를 계산하고
    가중 선형 결합으로 VSI를 산출합니다.

    공식: VSI = w_s * SVF + w_g * (1 - GVI) + w_b * BVI

    (1 - GVI) 변환은 논문·특허 모두 동일 — 식생 결핍이 클수록
    열노출이 크다는 물리적 의미를 반영하여 방향을 통일.

    Args:
        views_5: 5개 시야 세그멘테이션 (front, back, left, right, up).
        weights: (w_svf, w_gvi, w_bvi). 기본값은 논문 검증값.

    Returns:
        VSIResult — 중간 지표 포함한 최종 결과.

    Raises:
        ValueError: views_5가 5개가 아니거나 방향이 맞지 않을 때.
    """
    if len(views_5) != 5:
        raise ValueError(f"Expected 5 views, got {len(views_5)}")

    up_views = [v for v in views_5 if v.direction == "up"]
    if len(up_views) != 1:
        raise ValueError(f"Expected exactly 1 up view, got {len(up_views)}")

    horizontal_views = [v for v in views_5 if v.direction != "up"]

    svf = compute_svf(up_views[0])
    gvi = compute_gvi(horizontal_views)
    bvi = compute_bvi(horizontal_views)

    w_s, w_g, w_b = weights
    vsi = w_s * svf + w_g * (1.0 - gvi) + w_b * bvi

    return VSIResult(
        svf=svf,
        gvi=gvi,
        bvi=bvi,
        vsi=vsi,
        weights=weights,
        category=_classify_vsi(vsi),
    )


def compute_vsi_from_components(
    svf: float,
    gvi: float,
    bvi: float,
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
) -> VSIResult:
    """이미 산출된 SVF/GVI/BVI로 VSI만 계산 (테스트·비교용).

    논문 결과를 재현하거나 다른 가중치 조합을 탐색할 때 사용.
    """
    for name, value in (("svf", svf), ("gvi", gvi), ("bvi", bvi)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name}={value} must be in [0, 1]")

    w_s, w_g, w_b = weights
    vsi = w_s * svf + w_g * (1.0 - gvi) + w_b * bvi

    return VSIResult(
        svf=svf,
        gvi=gvi,
        bvi=bvi,
        vsi=vsi,
        weights=weights,
        category=_classify_vsi(vsi),
    )
