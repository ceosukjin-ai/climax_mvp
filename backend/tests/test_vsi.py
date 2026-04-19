"""
VSI 엔진 단위 테스트 + 논문 결과 재현 검증.

가장 중요한 테스트: 논문의 이론 범위, 임계값, 분류 로직이 정확히
재현되는지 확인. MVP 심사에서 "이 엔진이 논문 결과를 재현합니다"를
증명하는 핵심 근거가 됩니다.
"""
from __future__ import annotations

import math

import pytest

from app.core.vsi import (
    DEFAULT_WEIGHTS,
    PATENT_EXAMPLE_WEIGHTS,
    VSI_THRESHOLD_HIGH,
    VSI_THRESHOLD_LOW,
    ViewSegmentation,
    compute_bvi,
    compute_gvi,
    compute_svf,
    compute_vsi,
    compute_vsi_from_components,
)


# ===== 헬퍼 =====

def make_views(
    svf: float,
    gvi: float,
    bvi: float,
) -> list[ViewSegmentation]:
    """SVF/GVI/BVI 목표값을 그대로 산출하는 5-view 생성."""
    up = ViewSegmentation(
        direction="up",
        sky_ratio=svf,
        vegetation_ratio=0.0,
        building_ratio=0.0,
    )
    horizontals = [
        ViewSegmentation(
            direction=d,
            sky_ratio=0.0,
            vegetation_ratio=gvi,
            building_ratio=bvi,
        )
        for d in ("front", "back", "left", "right")
    ]
    return [up] + horizontals


# ===== 구성 요소 테스트 =====


class TestSVF:
    def test_svf_basic(self) -> None:
        up = ViewSegmentation(
            direction="up", sky_ratio=0.6, vegetation_ratio=0.0, building_ratio=0.0
        )
        assert compute_svf(up) == pytest.approx(0.6)

    def test_svf_requires_up_direction(self) -> None:
        wrong = ViewSegmentation(
            direction="front", sky_ratio=0.5, vegetation_ratio=0.0, building_ratio=0.0
        )
        with pytest.raises(ValueError, match="up view"):
            compute_svf(wrong)

    def test_svf_bounds(self) -> None:
        with pytest.raises(ValueError):
            ViewSegmentation(
                direction="up", sky_ratio=1.5, vegetation_ratio=0.0, building_ratio=0.0
            )


class TestGVI:
    def test_gvi_average_of_four_views(self) -> None:
        views = [
            ViewSegmentation(
                direction=d, sky_ratio=0.0, vegetation_ratio=v, building_ratio=0.0
            )
            for d, v in zip(
                ("front", "back", "left", "right"), (0.1, 0.2, 0.3, 0.4)
            )
        ]
        assert compute_gvi(views) == pytest.approx(0.25)

    def test_gvi_requires_exactly_four(self) -> None:
        views = [
            ViewSegmentation(
                direction=d, sky_ratio=0.0, vegetation_ratio=0.2, building_ratio=0.0
            )
            for d in ("front", "back", "left")
        ]
        with pytest.raises(ValueError, match="4 horizontal"):
            compute_gvi(views)


class TestBVI:
    def test_bvi_average(self) -> None:
        views = [
            ViewSegmentation(
                direction=d, sky_ratio=0.0, vegetation_ratio=0.0, building_ratio=b
            )
            for d, b in zip(
                ("front", "back", "left", "right"), (0.5, 0.5, 0.5, 0.5)
            )
        ]
        assert compute_bvi(views) == pytest.approx(0.5)


# ===== 최종 VSI 테스트 =====


class TestComputeVSI:
    def test_vsi_default_weights(self) -> None:
        views = make_views(svf=0.5, gvi=0.2, bvi=0.35)
        result = compute_vsi(views)

        assert result.svf == pytest.approx(0.5)
        assert result.gvi == pytest.approx(0.2)
        assert result.bvi == pytest.approx(0.35)
        # VSI = 0.5*0.5 + 0.3*(1-0.2) + 0.2*0.35 = 0.25 + 0.24 + 0.07 = 0.56
        assert result.vsi == pytest.approx(0.56)
        assert result.weights == DEFAULT_WEIGHTS

    def test_vsi_patent_example_weights(self) -> None:
        """특허 명세서 도면 2의 대표 실시예 (1, 1, 1)."""
        views = make_views(svf=0.5, gvi=0.2, bvi=0.35)
        result = compute_vsi(views, weights=PATENT_EXAMPLE_WEIGHTS)
        # VSI = 0.5 + 0.8 + 0.35 = 1.65
        assert result.vsi == pytest.approx(1.65)

    def test_vsi_rejects_non_5_views(self) -> None:
        views = make_views(0.5, 0.2, 0.3)[:4]
        with pytest.raises(ValueError, match="5 views"):
            compute_vsi(views)

    def test_vsi_rejects_duplicate_up(self) -> None:
        up = ViewSegmentation(
            direction="up", sky_ratio=0.5, vegetation_ratio=0.0, building_ratio=0.0
        )
        views = [up, up] + [
            ViewSegmentation(
                direction=d, sky_ratio=0.0, vegetation_ratio=0.2, building_ratio=0.3
            )
            for d in ("front", "back", "left")
        ]
        with pytest.raises(ValueError, match="1 up view"):
            compute_vsi(views)


# ===== 논문 재현 테스트 =====


@pytest.mark.paper_reproduction
class TestPaperReproduction:
    """논문 "Visual Spatial Index" 핵심 결과 재현.

    논문 Table 3의 임계값·분류 로직이 구현에 반영되었는지 검증합니다.
    """

    def test_threshold_constants_match_paper(self) -> None:
        """논문 Table 3: Low < 0.56, Moderate 0.56~0.71, High > 0.71."""
        assert VSI_THRESHOLD_LOW == 0.56
        assert VSI_THRESHOLD_HIGH == 0.71

    def test_low_classification(self) -> None:
        result = compute_vsi_from_components(svf=0.3, gvi=0.5, bvi=0.2)
        # 0.5*0.3 + 0.3*0.5 + 0.2*0.2 = 0.15 + 0.15 + 0.04 = 0.34
        assert result.vsi == pytest.approx(0.34)
        assert result.category == "Low"

    def test_moderate_classification(self) -> None:
        # VSI = 0.60 (Moderate 범위)
        result = compute_vsi_from_components(svf=0.5, gvi=0.15, bvi=0.25)
        # 0.25 + 0.255 + 0.05 = 0.555 (딱 경계 근처)
        # 좀 더 안쪽 값으로
        result = compute_vsi_from_components(svf=0.6, gvi=0.15, bvi=0.2)
        # 0.30 + 0.255 + 0.04 = 0.595
        assert 0.56 <= result.vsi <= 0.71
        assert result.category == "Moderate"

    def test_high_classification(self) -> None:
        # 건물 캐년 타입의 전형 — SVF·BVI 모두 높음
        result = compute_vsi_from_components(svf=0.9, gvi=0.05, bvi=0.7)
        # 0.45 + 0.285 + 0.14 = 0.875
        assert result.vsi > 0.71
        assert result.category == "High"

    def test_paper_theoretical_range_min(self) -> None:
        """논문 공식 이론 최솟값: SVF=0, GVI=1, BVI=0 → 0.3*0 = 0.0.

        실제로는 GVI=1이 드물지만 경계 검증.
        """
        result = compute_vsi_from_components(svf=0.0, gvi=1.0, bvi=0.0)
        assert result.vsi == pytest.approx(0.0)

    def test_paper_theoretical_range_max(self) -> None:
        """이론 최댓값: SVF=1, GVI=0, BVI=1 → 0.5 + 0.3 + 0.2 = 1.0."""
        result = compute_vsi_from_components(svf=1.0, gvi=0.0, bvi=1.0)
        assert result.vsi == pytest.approx(1.0)

    @pytest.mark.parametrize(
        "urban_type, svf, gvi, bvi, expected_category",
        [
            # 논문 3.2절 각 유형의 전형적 범위 중앙값
            ("Building Canyon (밀집 캐년)", 0.38, 0.12, 0.55, "Moderate"),
            ("Green (녹지 우세)", 0.47, 0.50, 0.20, "Low"),
            ("High exposure (개방+건물)", 0.80, 0.05, 0.55, "High"),
        ],
    )
    def test_urban_type_classification(
        self,
        urban_type: str,
        svf: float,
        gvi: float,
        bvi: float,
        expected_category: str,
    ) -> None:
        """논문 3.2절 각 도시 유형 전형 값 분류 테스트."""
        result = compute_vsi_from_components(svf=svf, gvi=gvi, bvi=bvi)
        assert result.category == expected_category, (
            f"{urban_type}: VSI={result.vsi:.3f} → {result.category} "
            f"(expected {expected_category})"
        )


# ===== 가중치 파라미터화 테스트 =====


class TestCustomWeights:
    def test_weights_affect_vsi(self) -> None:
        """가중치 변경이 VSI 값에 실제로 반영되는지."""
        default = compute_vsi_from_components(0.5, 0.2, 0.35)
        custom = compute_vsi_from_components(
            0.5, 0.2, 0.35, weights=(0.7, 0.2, 0.1)
        )
        assert default.vsi != custom.vsi
        # 0.7*0.5 + 0.2*0.8 + 0.1*0.35 = 0.35 + 0.16 + 0.035 = 0.545
        assert custom.vsi == pytest.approx(0.545)

    def test_weights_sum_free(self) -> None:
        """가중치 합이 1이 아니어도 동작 (특허 공식은 합=3)."""
        result = compute_vsi_from_components(
            0.5, 0.2, 0.35, weights=(1.0, 1.0, 1.0)
        )
        # 0.5 + 0.8 + 0.35 = 1.65
        assert result.vsi == pytest.approx(1.65)
