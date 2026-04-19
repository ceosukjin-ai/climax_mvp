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
        )
        serialized = original.to_bytes()
        restored = PanoAnalysisCache.from_bytes(serialized)
        assert restored == original


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

    async def test_aggregates_ratios_correctly(self) -> None:
        from app.services.orchestrator import VPTIOrchestrator
        from app.services.street_view import StreetViewFetchResult

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


class TestVSIConfiguration:
    """Step 2 진입 후에도 가중치 설정이 그대로 주입되는지."""

    def test_settings_weights_reachable(self) -> None:
        from app.config import get_settings

        s = get_settings()
        assert s.vsi_weights == (s.vsi_weight_svf, s.vsi_weight_gvi, s.vsi_weight_bvi)
        assert 0.0 < sum(s.vsi_weights) <= 3.0  # 허용 범위
