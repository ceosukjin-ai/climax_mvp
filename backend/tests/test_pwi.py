"""
PWI 엔진 단위 테스트.

물리 모델 기반이므로 단조성·범위·극한조건을 중점 검증.
"""
from __future__ import annotations

import pytest

from app.core.pwi import (
    PEDESTRIAN_HEIGHT_M,
    REFERENCE_HEIGHT_M,
    WindCondition,
    compute_pwi,
    downscale_to_pedestrian_height,
    urban_form_reduction,
)


class TestDownscale:
    def test_reduction_at_pedestrian_height(self) -> None:
        """1.5m는 10m보다 항상 느려야 함."""
        speed, reduction = downscale_to_pedestrian_height(10.0)
        assert 0.0 < reduction < 1.0
        assert speed < 10.0

    def test_zero_wind_stays_zero(self) -> None:
        speed, _ = downscale_to_pedestrian_height(0.0)
        assert speed == 0.0

    def test_higher_exponent_more_reduction(self) -> None:
        """거친 지표 (지수 ↑) → 감쇠 ↑."""
        _, r_smooth = downscale_to_pedestrian_height(10.0, exponent=0.15)
        _, r_rough = downscale_to_pedestrian_height(10.0, exponent=0.40)
        assert r_rough < r_smooth

    def test_negative_speed_raises(self) -> None:
        with pytest.raises(ValueError):
            downscale_to_pedestrian_height(-5.0)


class TestUrbanForm:
    def test_full_open_full_reduction(self) -> None:
        """SVF=1, BVI=0 → 감쇠 없음 (factor=1)."""
        f = urban_form_reduction(svf=1.0, bvi=0.0)
        assert f == pytest.approx(1.0)

    def test_closed_canyon_small_factor(self) -> None:
        """SVF=0.2, BVI=0.7 → 크게 감쇠."""
        f = urban_form_reduction(svf=0.2, bvi=0.7)
        assert 0.0 < f < 0.8

    def test_svf_dominates(self) -> None:
        """SVF가 0에 가까우면 바람이 매우 약해짐."""
        f_open = urban_form_reduction(0.9, 0.3)
        f_closed = urban_form_reduction(0.1, 0.3)
        assert f_closed < f_open


class TestComputePWI:
    def test_basic_pwi(self) -> None:
        wind = WindCondition(
            speed_ms=5.0, direction_deg=270.0, temperature_c=25.0
        )
        result = compute_pwi(wind, svf=0.6, bvi=0.3)

        assert 0.0 <= result.pwi <= 1.0
        assert result.pedestrian_wind_speed_ms < 5.0  # 항상 감쇠
        assert result.pedestrian_wind_speed_ms > 0.0

    def test_calm_classification(self) -> None:
        wind = WindCondition(
            speed_ms=0.5, direction_deg=180.0, temperature_c=20.0
        )
        result = compute_pwi(wind, svf=0.5, bvi=0.3)
        assert result.wind_chill_severity == "calm"

    def test_winter_strong_wind_hazardous(self) -> None:
        """저온 + 강풍 = hazardous (체감 한파)."""
        wind = WindCondition(
            speed_ms=15.0, direction_deg=0.0, temperature_c=-5.0
        )
        result = compute_pwi(wind, svf=0.9, bvi=0.1)
        assert result.wind_chill_severity == "hazardous"

    def test_zero_wind_calm(self) -> None:
        wind = WindCondition(
            speed_ms=0.0, direction_deg=0.0, temperature_c=20.0
        )
        result = compute_pwi(wind, svf=0.5, bvi=0.3)
        assert result.pedestrian_wind_speed_ms == 0.0
        assert result.pwi == 0.0

    def test_monotonic_in_wind_speed(self) -> None:
        """풍속이 커지면 PWI도 커져야 함 (같은 공간)."""
        results = []
        for speed in [1.0, 3.0, 5.0, 10.0]:
            wind = WindCondition(speed, 180.0, 20.0)
            results.append(compute_pwi(wind, 0.5, 0.3).pwi)
        assert results == sorted(results)
