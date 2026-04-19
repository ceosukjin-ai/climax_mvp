"""
SMTI 엔진 단위 테스트.

특허 명세서 7절 실시예를 재현하는 테스트 포함.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.core.smti import (
    MaterialFraction,
    SolarCondition,
    compute_smti,
    compute_solar_position,
    estimate_shading_from_svf,
)
from app.data.material_properties import MATERIAL_DB, get_properties


class TestMaterialDatabase:
    def test_asphalt_properties_match_patent(self) -> None:
        """특허 명세서 7절 실시예 표의 아스팔트 값 재현."""
        props = get_properties("asphalt")
        assert props.albedo == 0.05
        assert props.heat_capacity == 2.09
        assert props.emissivity == 0.95

    def test_concrete_properties_match_patent(self) -> None:
        props = get_properties("concrete")
        assert props.albedo == 0.30
        assert props.heat_capacity == 0.88
        assert props.emissivity == 0.92

    def test_vegetation_properties_match_patent(self) -> None:
        props = get_properties("vegetation")
        assert props.albedo == 0.20
        assert props.heat_capacity == 4.18
        assert props.emissivity == 0.98

    def test_glass_properties_match_patent(self) -> None:
        props = get_properties("glass")
        assert props.albedo == 0.10
        assert props.heat_capacity == 0.84
        assert props.emissivity == 0.84

    def test_unknown_material_falls_back(self) -> None:
        """DB에 없는 재질은 unknown으로 대체."""
        # type: ignore — 의도적으로 잘못된 타입 전달
        props = get_properties("martian_dust")  # type: ignore[arg-type]
        assert props == MATERIAL_DB["unknown"]


class TestSolarPosition:
    def test_seoul_noon_summer_high_elevation(self) -> None:
        """서울 하지 정오 태양 고도는 약 75°."""
        dt = datetime(2026, 6, 21, 12, 0, 0)
        solar = compute_solar_position(37.5665, 126.9780, dt)
        # 정오에 태양은 남쪽 근처 (방위각 130~200°), 고도 70~80°
        # 서울은 표준시 경도(135°)보다 서쪽이라 정남 도달이 12:30 전후
        assert 65.0 < solar.elevation_deg < 80.0
        assert 130.0 < solar.azimuth_deg < 210.0
        assert solar.is_daytime

    def test_seoul_midnight_negative_elevation(self) -> None:
        dt = datetime(2026, 6, 21, 0, 0, 0)
        solar = compute_solar_position(37.5665, 126.9780, dt)
        assert solar.elevation_deg < 0.0
        assert not solar.is_daytime
        assert solar.normalized_intensity == 0.0


class TestShading:
    def test_full_sky_view_no_shading(self) -> None:
        assert estimate_shading_from_svf(1.0) == 1.0

    def test_no_sky_full_shading(self) -> None:
        assert estimate_shading_from_svf(0.0) == 0.0

    def test_partial(self) -> None:
        assert estimate_shading_from_svf(0.5) == 0.5


class TestComputeSMTI:
    def test_smti_with_patent_example_materials(self) -> None:
        """특허 명세서 7절 실시예 재현.

        아스팔트 40% + 콘크리트 30% + 식생 20% + 유리 10%.
        특허는 구체적 최종 SMTI 값을 제시하지 않으므로,
        범위와 기여도 순서만 검증.
        """
        materials = [
            MaterialFraction(material="asphalt", fraction=0.4),
            MaterialFraction(material="concrete", fraction=0.3),
            MaterialFraction(material="vegetation", fraction=0.2),
            MaterialFraction(material="glass", fraction=0.1),
        ]
        solar = SolarCondition(
            elevation_deg=60.0, azimuth_deg=180.0, clearsky_ghi=900.0
        )
        result = compute_smti(
            materials=materials,
            solar=solar,
            shading_coefficient=1.0,  # 완전 노출
        )

        assert 0.0 <= result.smti <= 1.0
        # 아스팔트가 가장 큰 기여 (비율 40% + 흡수율 95%)
        sorted_contribs = sorted(
            result.material_contributions.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        assert sorted_contribs[0][0] == "asphalt"

    def test_shading_reduces_smti(self) -> None:
        """음영이 있으면 SMTI가 작아져야 함."""
        materials = [MaterialFraction(material="asphalt", fraction=1.0)]
        solar = SolarCondition(60.0, 180.0, 900.0)

        full_sun = compute_smti(materials, solar, shading_coefficient=1.0)
        full_shade = compute_smti(materials, solar, shading_coefficient=0.0)

        assert full_sun.smti > full_shade.smti
        assert full_shade.smti == pytest.approx(0.0, abs=0.01)

    def test_night_zero_solar_intensity(self) -> None:
        materials = [MaterialFraction(material="asphalt", fraction=1.0)]
        # 태양이 지평선 아래
        solar = SolarCondition(
            elevation_deg=-10.0, azimuth_deg=180.0, clearsky_ghi=0.0
        )
        result = compute_smti(materials, solar, shading_coefficient=1.0)
        assert result.solar_intensity == 0.0

    def test_empty_materials_raises(self) -> None:
        solar = SolarCondition(60.0, 180.0, 900.0)
        with pytest.raises(ValueError, match="empty"):
            compute_smti([], solar, 1.0)

    def test_vegetation_dominant_lower_smti(self) -> None:
        """식생이 지배적인 공간은 SMTI가 낮아야 함 (쿨링 효과)."""
        vegetation_heavy = [
            MaterialFraction(material="vegetation", fraction=0.8),
            MaterialFraction(material="concrete", fraction=0.2),
        ]
        asphalt_heavy = [
            MaterialFraction(material="asphalt", fraction=0.8),
            MaterialFraction(material="concrete", fraction=0.2),
        ]
        solar = SolarCondition(60.0, 180.0, 900.0)

        veg_result = compute_smti(vegetation_heavy, solar, 1.0)
        asp_result = compute_smti(asphalt_heavy, solar, 1.0)

        # 식생은 알베도 높고, 열용량도 높지만 반사가 크게 작용
        # 아스팔트는 알베도 매우 낮아 흡수 최대
        assert veg_result.smti < asp_result.smti
