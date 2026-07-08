"""
서비스 레이어 단위 테스트 (외부 API 모킹).

Step 2에서 추가한 Street View / KMA / 캐시 / 오케스트레이터의 핵심
로직을 검증합니다. 외부 API는 실제로 호출하지 않고 stub/mock으로 대체.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.cache import PanoAnalysisCache, WeatherCache
from app.services.kma import KMAGrid, latlon_to_grid


class TestKMAGridConversion:
    """기상청 격자 변환 공식 검증."""

    def test_seoul_city_hall(self) -> None:
        """서울시청 → 예상 격자 (60, 127)."""
        grid = latlon_to_grid(37.5665, 126.9780)
        assert grid.nx == 60
        assert grid.ny == 127

    def test_busan_pnu(self) -> None:
        """부산대학교 → 격자값."""
        grid = latlon_to_grid(35.2338, 129.0820)
        # 부산은 대략 nx=98, ny=76 근처
        assert 95 <= grid.nx <= 100
        assert 73 <= grid.ny <= 80

    def test_returns_kma_grid_instance(self) -> None:
        grid = latlon_to_grid(37.0, 127.0)
        assert isinstance(grid, KMAGrid)
        assert isinstance(grid.nx, int)
        assert isinstance(grid.ny, int)


class TestPanoAnalysisCacheSerialization:
    def test_round_trip(self) -> None:
        original = PanoAnalysisCache(
            pano_id="abc123",
            lat=37.5,
            lon=127.0,
            svf=0.65,
            gvi=0.15,
            bvi=0.25,
            material_ratios={"asphalt": 0.6, "concrete": 0.4},
            capture_date="2024-05-01",
            computed_at="2026-04-19T12:00:00Z",
            road_axis_deg=42.0,
            road_axis_source="osm",
        )
        serialized = original.to_bytes()
        restored = PanoAnalysisCache.from_bytes(serialized)
        assert restored == original

    def test_backward_compat_missing_road_axis(self) -> None:
        """구버전 캐시(도로축 필드 없음) → 기본값으로 역직렬화(하위호환)."""
        import orjson

        legacy = orjson.dumps({
            "pano_id": "old", "lat": 37.5, "lon": 127.0,
            "svf": 0.5, "gvi": 0.1, "bvi": 0.3,
            "material_ratios": {"asphalt": 1.0},
            "capture_date": None, "computed_at": "2026-01-01T00:00:00Z",
        })
        restored = PanoAnalysisCache.from_bytes(legacy)
        assert restored.road_axis_deg == 0.0
        assert restored.road_axis_source == "assumed"


class TestWeatherCacheSerialization:
    def test_round_trip(self) -> None:
        original = WeatherCache(
            nx=60,
            ny=127,
            temperature_c=25.0,
            humidity_pct=60.0,
            wind_speed_ms=2.5,
            wind_direction_deg=180.0,
            precipitation_mm=0.0,
            observed_at="2026-04-19T14:00:00+09:00",
            cached_at="2026-04-19T14:01:00Z",
        )
        assert WeatherCache.from_bytes(original.to_bytes()) == original


@pytest.mark.asyncio
class TestOrchestratorAnalyzeViews:
    """_analyze_views 헬퍼의 집계 로직 — 외부 API 없이 검증."""

    async def test_aggregates_ratios_correctly(self, monkeypatch) -> None:
        from app.services import orchestrator as orch_mod
        from app.services.orchestrator import VPTIOrchestrator
        from app.services.road_axis import RoadAxisResult
        from app.services.street_view import StreetViewFetchResult

        # 도로축은 Overpass 네트워크 → 스텁으로 대체(단위 테스트 격리).
        async def fake_road(lat, lon, **kwargs):  # noqa: ANN001, ANN202
            return RoadAxisResult(road_axis_deg=42.0, source="osm")

        monkeypatch.setattr(orch_mod, "get_road_axis", fake_road)

        # SegFormerService 모킹 — 각 이미지 세그멘테이션 결과를 스텁
        from app.ml.segformer import SegmentationOutput

        segformer_mock = MagicMock()

        def fake_segment(img_bytes: bytes) -> SegmentationOutput:
            # 모든 방향 동일하게 반환 (테스트 단순화)
            return SegmentationOutput(
                sky_ratio=0.6,
                vegetation_ratio=0.1,
                building_ratio=0.2,
                ground_ratio=0.1,
                material_ratios={"asphalt": 0.08, "concrete": 0.02},
                total_classified_pixels=409600,
            )

        segformer_mock.segment = fake_segment

        orch = VPTIOrchestrator(
            cache=MagicMock(),
            street_view=MagicMock(),
            kma=MagicMock(),
            segformer=segformer_mock,
        )

        sv_result = StreetViewFetchResult(
            pano_id="test_pano",
            lat=37.0,
            lon=127.0,
            images={
                "front": b"fake",
                "back": b"fake",
                "left": b"fake",
                "right": b"fake",
                "up": b"fake",
            },
            capture_date="2024-01-01",
        )

        analysis = await orch._analyze_views(sv_result)

        # up view sky_ratio = SVF
        assert analysis.svf == pytest.approx(0.6)
        # 수평 4방향 평균 (모두 0.1)
        assert analysis.gvi == pytest.approx(0.1)
        assert analysis.bvi == pytest.approx(0.2)
        # 재질 정규화
        assert sum(analysis.material_ratios.values()) == pytest.approx(1.0)
        # 아스팔트가 대부분
        assert analysis.material_ratios["asphalt"] > analysis.material_ratios["concrete"]
        # 도로축이 분석 결과에 실려 panoId 캐시에 함께 저장됨
        assert analysis.road_axis_deg == pytest.approx(42.0)
        assert analysis.road_axis_source == "osm"

    async def test_build_synthetic_views_roundtrip(self) -> None:
        """캐시 복원용 views는 원래 집계값을 재현해야 한다."""
        from app.core.vsi import compute_vsi
        from app.services.orchestrator import VPTIOrchestrator

        analysis = PanoAnalysisCache(
            pano_id="abc",
            lat=37.5,
            lon=127.0,
            svf=0.75,
            gvi=0.10,
            bvi=0.30,
            material_ratios={"asphalt": 1.0},
            capture_date=None,
            computed_at="2026-04-19T12:00:00Z",
        )
        views = VPTIOrchestrator._build_synthetic_views(analysis)
        result = compute_vsi(views)

        assert result.svf == pytest.approx(0.75)
        assert result.gvi == pytest.approx(0.10)
        assert result.bvi == pytest.approx(0.30)


@pytest.mark.asyncio
class TestOrchestratorPersonalized:
    """compute_personalized (자동 pVPTI) — 캐시 hit 경로, 외부 API/네트워크 없이 검증."""

    async def test_cached_path_returns_pvpti(self) -> None:
        from app.services.orchestrator import VPTIOrchestrator
        from vpti_core import Biometrics, PhysiologyProfile

        cache = MagicMock()
        cache.get_pano_id_for_location = AsyncMock(return_value="pano1")
        cache.get_pano_analysis = AsyncMock(
            return_value=PanoAnalysisCache(
                pano_id="pano1", lat=35.18901, lon=129.10069,
                svf=0.45, gvi=0.13, bvi=0.66,
                material_ratios={"asphalt": 0.6, "concrete": 0.3, "vegetation": 0.1},
                capture_date=None, computed_at="2026-07-15T00:00:00Z",
            )
        )
        cache.get_weather = AsyncMock(
            return_value=WeatherCache(
                nx=98, ny=76, temperature_c=31.0, humidity_pct=65.0,
                wind_speed_ms=2.5, wind_direction_deg=200.0, precipitation_mm=0.0,
                observed_at="2026-07-15T14:00:00+09:00", cached_at="2026-07-15T14:00:00Z",
            )
        )

        orch = VPTIOrchestrator(
            cache=cache, street_view=MagicMock(),
            kma=MagicMock(), segformer=MagicMock(),
        )
        bio = Biometrics(hr=118, activity=5.5, hr_rest=60)
        profile = PhysiologyProfile(age=40, sex="male", height_cm=175, weight_kg=72)

        result, tel = await orch.compute_personalized(
            lat=35.18901, lon=129.10069, bio=bio, profile=profile,
            timestamp=datetime(2026, 7, 15, 14, 0),
        )

        assert result.pvpti > 0
        assert result.metabolic_met is not None      # activity → met 적용
        assert result.season == "summer"
        # 캐시 hit 경로이므로 Street View·SegFormer 미호출
        assert tel.pano_cache_hit and tel.weather_cache_hit

    async def test_activity_absent_suppresses_strain(self) -> None:
        from app.services.orchestrator import VPTIOrchestrator
        from vpti_core import Biometrics

        cache = MagicMock()
        cache.get_pano_id_for_location = AsyncMock(return_value="p")
        cache.get_pano_analysis = AsyncMock(
            return_value=PanoAnalysisCache(
                pano_id="p", lat=35.0, lon=129.0, svf=0.5, gvi=0.1, bvi=0.6,
                material_ratios={"asphalt": 1.0}, capture_date=None,
                computed_at="2026-07-15T00:00:00Z",
            )
        )
        cache.get_weather = AsyncMock(
            return_value=WeatherCache(
                nx=98, ny=76, temperature_c=31.0, humidity_pct=60.0,
                wind_speed_ms=2.0, wind_direction_deg=180.0, precipitation_mm=0.0,
                observed_at="2026-07-15T14:00:00+09:00", cached_at="2026-07-15T14:00:00Z",
            )
        )
        orch = VPTIOrchestrator(cache=cache, street_view=MagicMock(),
                                kma=MagicMock(), segformer=MagicMock())
        result, _ = await orch.compute_personalized(
            lat=35.0, lon=129.0, bio=Biometrics(hr=150, activity=None, hr_rest=60),
            timestamp=datetime(2026, 7, 15, 14, 0),
        )
        assert result.strain_index == 0.0
        assert result.risk_level == result.base_risk_level


class TestVSIConfiguration:
    """Step 2 진입 후에도 가중치 설정이 그대로 주입되는지."""

    def test_settings_weights_reachable(self) -> None:
        from app.config import get_settings

        s = get_settings()
        assert s.vsi_weights == (s.vsi_weight_svf, s.vsi_weight_gvi, s.vsi_weight_bvi)
        assert 0.0 < sum(s.vsi_weights) <= 3.0  # 허용 범위
