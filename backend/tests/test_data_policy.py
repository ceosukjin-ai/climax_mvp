"""데이터 출처 정책 · 거리영상 쿼터 가드 테스트 (2026-08-21).

출시 전 법적 리스크 검토의 코드 측 대응이 실제로 동작하는지 확인한다.
- GSV 유래 파생 지표가 ML 학습 경로로 새어 나가지 않는가
- 캐시·DB에 영상 출처가 실제로 남는가 (구버전 데이터 호환 포함)
- 월 상한을 넘으면 신규 거리영상 다운로드가 멈추는가
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.data_policy import (
    GSV,
    MAPILLARY,
    OWN,
    DataPolicyViolation,
    assert_ml_trainable,
    filter_ml_trainable,
    ml_trainable,
    redistributable,
)
from app.services.cache import PanoAnalysisCache


class TestDataPolicy:
    def test_gsv_is_not_ml_trainable(self) -> None:
        """약관 3.2.3(c)(vii) — GSV 유래는 모델 학습에 쓸 수 없다."""
        assert ml_trainable(GSV) is False
        assert ml_trainable(MAPILLARY) is True
        assert ml_trainable(OWN) is True

    def test_unknown_source_defaults_to_forbidden(self) -> None:
        """출처를 모르면 보수적으로 금지 — 구버전 행(None)은 전부 GSV 유래다."""
        assert ml_trainable(None) is False
        assert ml_trainable("") is False

    def test_only_own_imagery_is_redistributable(self) -> None:
        assert redistributable(OWN) is True
        assert redistributable(MAPILLARY) is False   # CC BY-SA share-alike 주의
        assert redistributable(GSV) is False

    def test_assert_raises_when_gsv_mixed_in(self) -> None:
        """조용히 걸러내지 않고 멈춘다 — 데이터가 줄어든 걸 못 보고 넘어가면 안 된다."""
        with pytest.raises(DataPolicyViolation) as e:
            assert_ml_trainable([MAPILLARY, GSV, OWN])
        assert "gsv" in str(e.value)

    def test_assert_passes_for_allowed_sources(self) -> None:
        assert_ml_trainable([MAPILLARY, OWN, MAPILLARY])   # 예외 없음

    def test_filter_keeps_only_allowed(self) -> None:
        rows = [{"imagery_src": GSV}, {"imagery_src": OWN}, {"imagery_src": None}]
        assert filter_ml_trainable(rows) == [{"imagery_src": OWN}]


class TestPanoCacheImagerySource:
    def test_source_survives_round_trip(self) -> None:
        original = PanoAnalysisCache(
            pano_id="p1", lat=35.1, lon=129.1, svf=0.5, gvi=0.1, bvi=0.4,
            material_ratios={"asphalt": 1.0}, capture_date=None,
            computed_at="2026-08-21T00:00:00Z", imagery_source=MAPILLARY,
        )
        assert PanoAnalysisCache.from_bytes(original.to_bytes()) == original

    def test_legacy_cache_entry_defaults_to_gsv(self) -> None:
        """imagery_source 없던 기존 캐시 항목은 GSV로 읽혀야 한다(전부 GSV 유래)."""
        import orjson

        legacy = orjson.dumps({
            "pano_id": "old", "lat": 35.1, "lon": 129.1,
            "svf": 0.5, "gvi": 0.1, "bvi": 0.4,
            "material_ratios": {"asphalt": 1.0}, "capture_date": None,
            "computed_at": "2026-07-01T00:00:00Z",
        })
        restored = PanoAnalysisCache.from_bytes(legacy)
        assert restored.imagery_source == GSV
        assert ml_trainable(restored.imagery_source) is False


@pytest.mark.asyncio
class TestImageryBudgetGuard:
    """월 상한 — 과금 차단 + 약관 3.2.3(a)(ii) bulk download 방지."""

    @staticmethod
    def _orch(count: int, budget: int):
        from app.services.orchestrator import VPTIOrchestrator

        cache = MagicMock()
        cache.incr_imagery_fetch = AsyncMock(return_value=count)
        return VPTIOrchestrator(
            cache=cache, street_view=MagicMock(), kma=MagicMock(),
            segformer=MagicMock(), imagery_monthly_budget=budget,
        )

    async def test_under_budget_passes(self) -> None:
        await self._orch(count=100, budget=9000)._check_imagery_budget()

    async def test_over_budget_blocks_new_downloads(self) -> None:
        from app.services.street_view import StreetViewNotFound

        with pytest.raises(StreetViewNotFound):
            await self._orch(count=9001, budget=9000)._check_imagery_budget()

    async def test_budget_zero_disables_guard(self) -> None:
        """개발용 무제한 설정에서는 카운터를 건드리지도 않는다."""
        orch = self._orch(count=99_999, budget=0)
        await orch._check_imagery_budget()
        orch.cache.incr_imagery_fetch.assert_not_awaited()

    async def test_counter_failure_does_not_break_service(self) -> None:
        """Redis가 죽어도 측정은 계속돼야 한다."""
        from app.services.orchestrator import VPTIOrchestrator

        cache = MagicMock()
        cache.incr_imagery_fetch = AsyncMock(side_effect=RuntimeError("redis down"))
        orch = VPTIOrchestrator(
            cache=cache, street_view=MagicMock(), kma=MagicMock(),
            segformer=MagicMock(), imagery_monthly_budget=9000,
        )
        await orch._check_imagery_budget()   # 예외 없이 통과
