"""
VPTI 통합 엔진 테스트.

VSI·SMTI·PWI가 조합될 때 계절별 로직·행동 가이드·위험도 분류가
의도대로 작동하는지 검증.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.core.smti import MaterialFraction
from app.core.vpti import WeatherContext, compute_vpti
from app.core.vsi import ViewSegmentation


def make_full_input(svf: float, gvi: float, bvi: float) -> list[ViewSegmentation]:
    up = ViewSegmentation("up", sky_ratio=svf, vegetation_ratio=0, building_ratio=0)
    horizontals = [
        ViewSegmentation(
            d, sky_ratio=0, vegetation_ratio=gvi, building_ratio=bvi
        )
        for d in ("front", "back", "left", "right")
    ]
    return [up] + horizontals


@pytest.fixture
def summer_heat_scenario() -> dict:
    """여름 폭염 + 개방된 아스팔트 도로 (최악)."""
    return {
        "views_5": make_full_input(svf=0.9, gvi=0.05, bvi=0.2),
        "materials": [
            MaterialFraction(material="asphalt", fraction=0.7),
            MaterialFraction(material="concrete", fraction=0.3),
        ],
        "weather": WeatherContext(
            temperature_c=33.0,
            humidity_pct=70.0,
            wind_speed_ms=1.0,
            wind_direction_deg=180.0,
        ),
        "latitude": 35.2338,  # PNU
        "longitude": 129.0820,
        "timestamp": datetime(2026, 8, 1, 14, 0, 0),
    }


@pytest.fixture
def winter_cold_scenario() -> dict:
    """겨울 한파 + 개방된 광장 + 강풍."""
    return {
        "views_5": make_full_input(svf=0.95, gvi=0.0, bvi=0.1),
        "materials": [
            MaterialFraction(material="concrete", fraction=0.8),
            MaterialFraction(material="stone", fraction=0.2),
        ],
        "weather": WeatherContext(
            temperature_c=-8.0,
            humidity_pct=40.0,
            wind_speed_ms=12.0,
            wind_direction_deg=315.0,  # 북서풍
        ),
        "latitude": 37.5665,  # 서울
        "longitude": 126.9780,
        "timestamp": datetime(2026, 1, 15, 9, 0, 0),
    }


@pytest.fixture
def green_summer_scenario() -> dict:
    """여름 + 녹지 + 적당한 바람 (쾌적)."""
    return {
        "views_5": make_full_input(svf=0.4, gvi=0.55, bvi=0.15),
        "materials": [
            MaterialFraction(material="vegetation", fraction=0.6),
            MaterialFraction(material="soil", fraction=0.3),
            MaterialFraction(material="concrete", fraction=0.1),
        ],
        "weather": WeatherContext(
            temperature_c=27.0,
            humidity_pct=60.0,
            wind_speed_ms=4.0,
            wind_direction_deg=225.0,
        ),
        "latitude": 35.2338,
        "longitude": 129.0820,
        "timestamp": datetime(2026, 8, 1, 14, 0, 0),
    }


class TestVPTISummer:
    def test_summer_heat_detected_as_warning_or_worse(
        self, summer_heat_scenario
    ) -> None:
        result = compute_vpti(**summer_heat_scenario)
        assert result.season == "summer"
        assert result.risk_level in ("warning", "danger", "severe")
        # 체감은 실제 기온보다 높아야 함 (복사열 + 표면열)
        assert result.vpti > summer_heat_scenario["weather"].temperature_c

    def test_summer_green_cooler(self, green_summer_scenario) -> None:
        result = compute_vpti(**green_summer_scenario)
        assert result.season == "summer"
        # 녹지는 safe~caution 수준
        assert result.risk_level in ("safe", "caution")

    def test_summer_dominant_cause_explained(
        self, summer_heat_scenario
    ) -> None:
        result = compute_vpti(**summer_heat_scenario)
        # 폭염 시나리오에서 행동 가이드는 비어있지 않아야 함
        assert len(result.action_guide) > 10
        # 공간·재질·바람 중 하나가 체감을 크게 올렸어야 함
        contribs = [
            abs(result.contribution_space),
            abs(result.contribution_material),
            abs(result.contribution_wind),
        ]
        assert max(contribs) > 0.5


class TestVPTIWinter:
    def test_winter_wind_chill_detected(self, winter_cold_scenario) -> None:
        result = compute_vpti(**winter_cold_scenario)
        assert result.season == "winter"
        # 바람 기여는 음수 (체감 한파)
        assert result.contribution_wind < 0.0

    def test_winter_severe_with_strong_wind(
        self, winter_cold_scenario
    ) -> None:
        result = compute_vpti(**winter_cold_scenario)
        # -8°C + 강풍 → 심각
        assert result.risk_level in ("danger", "severe", "warning")
        assert result.vpti < winter_cold_scenario["weather"].temperature_c


class TestVPTIResponseStructure:
    def test_all_components_returned(self, summer_heat_scenario) -> None:
        result = compute_vpti(**summer_heat_scenario)
        d = result.as_dict()

        # 최상위 필드
        assert "vpti" in d
        assert "risk_level" in d
        assert "season" in d
        assert "action_guide" in d

        # 각 지수
        assert "vsi" in d and "vsi" in d["vsi"]
        assert "smti" in d and "smti" in d["smti"]
        assert "pwi" in d and "pwi" in d["pwi"]

        # 원인 분해
        assert set(d["contributions"].keys()) == {"space", "material", "wind"}

    def test_timestamp_iso_format(self, summer_heat_scenario) -> None:
        result = compute_vpti(**summer_heat_scenario)
        # ISO 8601 형식 확인
        datetime.fromisoformat(result.timestamp)
