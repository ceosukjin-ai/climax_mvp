"""
VSI (Visual Spatial Index) — 시각 환경 지수.

특허: P2026-0082-KR00 (다방향 시각 영상을 이용한 시각 환경 지수 산출 방법)

【수학식 1】 (명세서 26쪽)
    VSI = w1·SVF + w2·(1 − GVI) + w3·BVI

  · SVF (Sky View Factor)   : 상향 영상의 하늘 영역 면적 비율 [0, 1]
  · GVI (Green View Index)  : 수평 4방향(전·후·좌·우) 식생 영역 평균 면적 비율
  · BVI (Building View Index): 수평 4방향 건축 영역 평균 면적 비율
  · (1 − GVI) = 식생 결핍도 (명세서: 증발산·차광 효과 상실 정도)

가중치 w1=0.5, w2=0.3, w3=0.2 는 ✅ 특허 명시값(config.VSIConfig).
SVF는 상향 영상에서, GVI·BVI는 수평 4뷰에서 산출한다(명세서 25~26쪽, 도 7).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .config import DEFAULT_CONFIG, VSIConfig

ViewDirection = Literal["front", "back", "left", "right", "up"]
HORIZONTAL_VIEWS: tuple[ViewDirection, ...] = ("front", "back", "left", "right")


@dataclass(frozen=True, slots=True)
class ViewSegmentation:
    """단일 시야 방향의 세그멘테이션 면적 비율.

    특허 도 4: 다방향 시각 영상을 하늘/식생/건축 영역으로 분할한 결과.
    """

    direction: ViewDirection
    sky_ratio: float          # 하늘 영역 면적 비율 [0, 1]
    vegetation_ratio: float   # 식생 영역 면적 비율 [0, 1]
    building_ratio: float     # 건축 영역 면적 비율 [0, 1]

    def __post_init__(self) -> None:
        for name, value in (
            ("sky_ratio", self.sky_ratio),
            ("vegetation_ratio", self.vegetation_ratio),
            ("building_ratio", self.building_ratio),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{name}={value} must be in [0, 1] (direction={self.direction})"
                )


@dataclass(frozen=True, slots=True)
class VSIComponents:
    """수학식 1의 입력 3종 — 다른 지수(PWI 등)가 재사용."""

    svf: float
    gvi: float
    bvi: float


@dataclass(frozen=True, slots=True)
class VSIResult:
    svf: float
    gvi: float
    bvi: float
    vsi: float
    weights: tuple[float, float, float]

    @property
    def components(self) -> VSIComponents:
        return VSIComponents(svf=self.svf, gvi=self.gvi, bvi=self.bvi)

    def as_dict(self) -> dict:
        return {
            "svf": round(self.svf, 4),
            "gvi": round(self.gvi, 4),
            "bvi": round(self.bvi, 4),
            "vsi": round(self.vsi, 4),
            "weights": self.weights,
        }


def reconstruct_svf(
    views_5: list[ViewSegmentation],
    split_deg: float = 45.0,
) -> float:
    """5뷰 상단을 입체각 가중 합산한 반구 SVF 근사.

    단일 천정샷(up.sky_ratio)은 트인 천정만 표본해 협곡에서도 1.0으로 포화한다.
    이를 보정하려고 상향 반구를 두 입체각 영역으로 나눠 결합한다:

      · 천정 캡 (고도 split~90°)  ← 상향(up) 영상 하늘비율 그대로
      · 하부 링 (고도 0~split°)   ← 수평 4뷰의 "지평선 위" 하늘비율

    수평뷰는 pitch=0·fov=90 라 고도 −45~+45° 를 담고, 하늘은 지평선 위(이미지
    상단 절반)에만 존재하므로 상단 절반 하늘비율 ≈ min(1, 2·sky_ratio) 로 근사한다.

    입체각 가중 (균일 반구, Ω=2π):
        w_cap  = 1 − cos(split)   (천정 캡 비율)
        w_ring = cos(split)       (하부 링 비율),   w_cap + w_ring = 1
        SVF = w_cap·(천정 하늘) + w_ring·(링 하늘)

    split=45° 는 상향뷰(고도 45~90°)와 수평뷰 상단(0~45°)이 정확히 맞물리는 기본값.
    """
    by_dir = {v.direction: v for v in views_5}
    up_sky = by_dir["up"].sky_ratio
    horizontals = [by_dir[d] for d in HORIZONTAL_VIEWS]
    ring_sky = sum(min(1.0, 2.0 * v.sky_ratio) for v in horizontals) / len(horizontals)

    split = math.radians(split_deg)
    w_cap = 1.0 - math.cos(split)
    w_ring = math.cos(split)
    return w_cap * up_sky + w_ring * ring_sky


def extract_components(
    views_5: list[ViewSegmentation],
    config: VSIConfig = DEFAULT_CONFIG.vsi,
) -> VSIComponents:
    """5-view 세그멘테이션에서 SVF·GVI·BVI 산출 (특허 도 7).

    SVF  = config.svf_method 에 따라:
             "zenith"    → 상향 영상의 하늘 면적 비율 (특허 도 7 원문).
             "multiview" → 5뷰 입체각 가중 반구 근사 (reconstruct_svf, 기본).
    GVI  = 수평 4방향 식생 면적 비율의 평균.
    BVI  = 수평 4방향 건축 면적 비율의 평균.
    """
    if len(views_5) != 5:
        raise ValueError(f"5-view가 필요함, got {len(views_5)}개")

    by_dir = {v.direction: v for v in views_5}
    if set(by_dir) != {"front", "back", "left", "right", "up"}:
        raise ValueError(f"방향 집합 불일치: {sorted(by_dir)}")

    up = by_dir["up"]
    horizontals = [by_dir[d] for d in HORIZONTAL_VIEWS]

    if config.svf_method == "multiview":
        svf = reconstruct_svf(views_5, config.svf_horizon_split_deg)
    else:
        svf = up.sky_ratio
    svf = min(max(svf, 0.0), 1.0)
    gvi = sum(v.vegetation_ratio for v in horizontals) / len(horizontals)
    bvi = sum(v.building_ratio for v in horizontals) / len(horizontals)
    return VSIComponents(svf=svf, gvi=gvi, bvi=bvi)


def compute_vsi_from_components(
    components: VSIComponents,
    config: VSIConfig = DEFAULT_CONFIG.vsi,
) -> VSIResult:
    """수학식 1을 그대로 적용: VSI = w1·SVF + w2·(1−GVI) + w3·BVI."""
    svf, gvi, bvi = components.svf, components.gvi, components.bvi
    for name, value in (("svf", svf), ("gvi", gvi), ("bvi", bvi)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name}={value} must be in [0, 1]")

    w1, w2, w3 = config.w_svf, config.w_gvi, config.w_bvi
    vsi = w1 * svf + w2 * (1.0 - gvi) + w3 * bvi  # ← 수학식 1

    return VSIResult(svf=svf, gvi=gvi, bvi=bvi, vsi=vsi, weights=(w1, w2, w3))


def compute_vsi(
    views_5: list[ViewSegmentation],
    config: VSIConfig = DEFAULT_CONFIG.vsi,
) -> VSIResult:
    """5-view 세그멘테이션 → VSI 전체 파이프라인."""
    return compute_vsi_from_components(extract_components(views_5, config), config)
