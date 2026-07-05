"""
VPTI 실시간 파이프라인.

입력: (lat, lon, timestamp)
출력: VPTIResult

흐름:
1. 좌표 → panoId (캐시 또는 Metadata API)
2. panoId 공간분석 캐시 확인
   - hit: Redis에서 VSI 구성요소 + 재질 비율 로드
   - miss: Street View 5-view fetch → SegFormer 추론 → 캐시 저장
3. 기상 조회 (격자당 10분 캐시)
4. VPTI 산출 (core.vpti.compute_vpti)
5. 결과 반환

첫 방문자 지연: 1~3초
재방문자 지연: <100ms
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from loguru import logger

from app.core.smti import MaterialFraction
from app.core.vpti import VPTIResult, WeatherContext, compute_vpti
from app.core.vsi import ViewSegmentation
from app.services.cache import (
    CacheService,
    PanoAnalysisCache,
    WeatherCache,
)
from app.services.kma import KMAClient, KMAObservation, KST, latlon_to_grid
from app.services.street_view import (
    GoogleStreetViewClient,
    StreetViewFetchResult,
    StreetViewNotFound,
)

if TYPE_CHECKING:
    from app.ml.segformer import SegFormerService


@dataclass(frozen=True, slots=True)
class PipelineTelemetry:
    """요청별 성능 추적. WebSocket에서 frontend에 보내 모니터링."""

    pano_cache_hit: bool
    weather_cache_hit: bool
    total_ms: float
    street_view_ms: float
    segmentation_ms: float
    weather_ms: float


class VPTIOrchestrationError(Exception):
    """파이프라인 상위 오류."""


class VPTIOrchestrator:
    """실시간 VPTI 파이프라인 오케스트레이션.

    외부 의존성을 생성자 주입 받아 테스트·모킹을 쉽게 만듭니다.
    """

    def __init__(
        self,
        cache: CacheService,
        street_view: GoogleStreetViewClient,
        kma: KMAClient,
        segformer: "SegFormerService",
    ) -> None:
        self.cache = cache
        self.street_view = street_view
        self.kma = kma
        self.segformer = segformer

    # ===== panoId 해석 =====

    async def _resolve_pano_id(self, lat: float, lon: float) -> tuple[str, float, float]:
        """좌표 → panoId.

        1차: Redis 좌표 매핑
        2차: Google Metadata API → 캐시 저장
        """
        cached = await self.cache.get_pano_id_for_location(lat, lon)
        if cached:
            return cached, lat, lon

        meta = await self.street_view.get_pano_metadata(lat, lon)
        if meta.status != "OK" or not meta.pano_id:
            raise StreetViewNotFound(
                f"No Street View at ({lat}, {lon}): {meta.status}"
            )

        await self.cache.set_pano_id_for_location(lat, lon, meta.pano_id)
        return meta.pano_id, meta.lat, meta.lon

    # ===== 공간 분석 =====

    async def _get_or_compute_pano_analysis(
        self,
        pano_id: str,
        lat: float,
        lon: float,
    ) -> tuple[PanoAnalysisCache, bool, float, float]:
        """공간 분석 결과 조회 또는 계산.

        Returns:
            (cache, is_cache_hit, street_view_ms, segmentation_ms)
        """
        cached = await self.cache.get_pano_analysis(pano_id)
        if cached is not None:
            return cached, True, 0.0, 0.0

        # 캐시 miss: Street View fetch + SegFormer 추론
        logger.info("Pano cache MISS, fetching and analyzing: {}", pano_id)

        sv_start = asyncio.get_event_loop().time()
        sv_result = await self._fetch_with_metadata(pano_id, lat, lon)
        sv_ms = (asyncio.get_event_loop().time() - sv_start) * 1000

        seg_start = asyncio.get_event_loop().time()
        analysis = await self._analyze_views(sv_result)
        seg_ms = (asyncio.get_event_loop().time() - seg_start) * 1000

        await self.cache.set_pano_analysis(analysis)
        return analysis, False, sv_ms, seg_ms

    async def _fetch_with_metadata(
        self, pano_id: str, lat: float, lon: float
    ) -> StreetViewFetchResult:
        """panoId 알고 있을 때 직접 5-view fetch.

        Metadata를 재요청하지 않고 바로 이미지만 받기 위해 임시 metadata 구성.
        """
        from app.services.street_view import PanoMetadata

        fake_meta = PanoMetadata(
            pano_id=pano_id, lat=lat, lon=lon, date=None, status="OK"
        )
        return await self.street_view.fetch_five_views(fake_meta)

    async def _analyze_views(
        self, sv_result: StreetViewFetchResult
    ) -> PanoAnalysisCache:
        """5-view → 세그멘테이션 → SVF/GVI/BVI/재질비율.

        SegFormer 추론은 CPU-bound이므로 to_thread로 이벤트루프에서 분리.
        """
        # 5개 방향 병렬 추론 (단, CPU-bound이므로 실질적으로 순차에 가까움)
        tasks = [
            asyncio.to_thread(self.segformer.segment, img_bytes)
            for img_bytes in sv_result.images.values()
        ]
        segmentations = await asyncio.gather(*tasks)
        direction_to_seg = dict(zip(sv_result.images.keys(), segmentations))

        # SVF — 상향 시야 하늘 비율
        svf = direction_to_seg["up"].sky_ratio

        # GVI/BVI — 수평 4방향 평균
        horizontal_segs = [
            direction_to_seg[d] for d in ("front", "back", "left", "right")
        ]
        gvi = sum(s.vegetation_ratio for s in horizontal_segs) / 4
        bvi = sum(s.building_ratio for s in horizontal_segs) / 4

        # 재질 비율 — 수평 4방향 합산 (지면이 주로 수평 아래쪽에 보임)
        material_ratios: dict[str, float] = {}
        for seg in horizontal_segs:
            for mat, ratio in seg.material_ratios.items():
                material_ratios[mat] = material_ratios.get(mat, 0.0) + ratio
        # 4방향 평균
        material_ratios = {
            k: v / 4 for k, v in material_ratios.items() if v > 0
        }
        # 재질 비율 정규화 (합이 1이 되도록)
        total = sum(material_ratios.values())
        if total > 0:
            material_ratios = {k: v / total for k, v in material_ratios.items()}
        else:
            material_ratios = {"unknown": 1.0}

        return PanoAnalysisCache(
            pano_id=sv_result.pano_id,
            lat=sv_result.lat,
            lon=sv_result.lon,
            svf=svf,
            gvi=gvi,
            bvi=bvi,
            material_ratios=material_ratios,
            capture_date=sv_result.capture_date,
            computed_at=datetime.now(timezone.utc).isoformat() + "Z",
        )

    # ===== 기상 조회 =====

    async def _get_weather(
        self, lat: float, lon: float
    ) -> tuple[WeatherContext, bool, float]:
        """기상 조회. 격자 단위 캐싱.

        Returns:
            (weather, is_cache_hit, elapsed_ms)
        """
        grid = latlon_to_grid(lat, lon)

        cached = await self.cache.get_weather(grid.nx, grid.ny)
        if cached is not None:
            return (
                WeatherContext(
                    temperature_c=cached.temperature_c,
                    humidity_pct=cached.humidity_pct,
                    wind_speed_ms=cached.wind_speed_ms,
                    wind_direction_deg=cached.wind_direction_deg,
                    precipitation_mm=cached.precipitation_mm,
                ),
                True,
                0.0,
            )

        start = asyncio.get_event_loop().time()
        obs = await self.kma.get_current_observation(lat, lon)
        elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000

        weather_cache = WeatherCache(
            nx=grid.nx,
            ny=grid.ny,
            temperature_c=obs.temperature_c,
            humidity_pct=obs.humidity_pct,
            wind_speed_ms=obs.wind_speed_ms,
            wind_direction_deg=obs.wind_direction_deg,
            precipitation_mm=obs.precipitation_mm,
            observed_at=obs.observed_at.isoformat(),
            cached_at=datetime.now(timezone.utc).isoformat() + "Z",
        )
        await self.cache.set_weather(weather_cache)

        return (
            WeatherContext(
                temperature_c=obs.temperature_c,
                humidity_pct=obs.humidity_pct,
                wind_speed_ms=obs.wind_speed_ms,
                wind_direction_deg=obs.wind_direction_deg,
                precipitation_mm=obs.precipitation_mm,
            ),
            False,
            elapsed_ms,
        )

    # ===== 메인 파이프라인 =====

    async def compute(
        self,
        lat: float,
        lon: float,
        timestamp: datetime | None = None,
    ) -> tuple[VPTIResult, PipelineTelemetry]:
        """전체 파이프라인 실행."""
        loop_start = asyncio.get_event_loop().time()

        # 1. panoId 해석
        pano_id, canonical_lat, canonical_lon = await self._resolve_pano_id(lat, lon)

        # 2 & 3 병렬 실행: 공간 분석 + 기상 조회
        pano_task = self._get_or_compute_pano_analysis(
            pano_id, canonical_lat, canonical_lon
        )
        weather_task = self._get_weather(canonical_lat, canonical_lon)

        (pano_analysis, pano_hit, sv_ms, seg_ms), (
            weather,
            weather_hit,
            weather_ms,
        ) = await asyncio.gather(pano_task, weather_task)

        # 4. VPTI 산출
        # 공간 분석 결과를 엔진이 기대하는 형태로 변환
        views_5 = self._build_synthetic_views(pano_analysis)
        materials = self._build_material_fractions(pano_analysis.material_ratios)

        vpti_result = compute_vpti(
            views_5=views_5,
            materials=materials,
            weather=weather,
            latitude=canonical_lat,
            longitude=canonical_lon,
            timestamp=timestamp,
        )

        total_ms = (asyncio.get_event_loop().time() - loop_start) * 1000
        telemetry = PipelineTelemetry(
            pano_cache_hit=pano_hit,
            weather_cache_hit=weather_hit,
            total_ms=total_ms,
            street_view_ms=sv_ms,
            segmentation_ms=seg_ms,
            weather_ms=weather_ms,
        )

        logger.info(
            "VPTI {} | pano_hit={} weather_hit={} total={:.0f}ms",
            "cached" if pano_hit and weather_hit else "computed",
            pano_hit,
            weather_hit,
            total_ms,
        )

        return vpti_result, telemetry

    # ===== 강수 컨텍스트 (VPTI와 분리된 외부 예보 레이어) =====

    async def get_precipitation_outlook(self, lat: float, lon: float) -> dict:
        """현재 강수 + 0~6시간 강수 전망.

        비는 거리 기하로 예측 불가 → 순수 KMA 외부 데이터로만 다루고, VPTI 숫자에는
        섞지 않는다(별도 컨텍스트 레이어). 정확도를 위해 단기예보 POP 대신 초단기예보
        (0~6h)를 사용한다.
        """
        # 현재 강수량은 이미 캐시된 실황에서 재사용
        weather, _, _ = await self._get_weather(lat, lon)
        now_precip = weather.precipitation_mm

        try:
            forecasts = await self.kma.get_ultra_short_forecast(lat, lon)
        except Exception as e:  # noqa: BLE001
            logger.warning("강수 전망 조회 실패: {}", e)
            forecasts = []

        now = datetime.now(KST)
        hourly: list[dict] = []
        onset_hours: int | None = None
        max_precip = now_precip
        for f in forecasts[:6]:
            pty = f.precipitation_type or "없음"
            pmm = f.precipitation_mm or 0.0
            is_rain = pty not in ("없음", None) or pmm > 0.0
            hrs = max(0, round((f.forecast_for - now).total_seconds() / 3600))
            hourly.append({
                "time": f.forecast_for.strftime("%H:%M"),
                "in_hours": hrs,
                "pty": pty,
                "sky": f.sky_condition,
                "precip_mm": round(pmm, 1),
            })
            if is_rain:
                max_precip = max(max_precip, pmm)
                if onset_hours is None:
                    onset_hours = hrs

        raining_now = now_precip > 0.0
        rain_expected = raining_now or onset_hours is not None
        umbrella = rain_expected

        if raining_now:
            advice = "현재 비가 내리고 있습니다 — 우산 필요"
        elif onset_hours is not None:
            advice = f"약 {onset_hours}시간 후 비 예보 — 우산 챙기세요"
        else:
            advice = "6시간 내 비 예보 없음"

        return {
            "raining_now": raining_now,
            "current_precip_mm": round(now_precip, 1),
            "rain_expected_6h": rain_expected,
            "onset_in_hours": onset_hours,
            "max_precip_mm": round(max_precip, 1),
            "umbrella_recommended": umbrella,
            "advice": advice,
            "hourly": hourly,
        }

    @staticmethod
    def _build_synthetic_views(
        analysis: PanoAnalysisCache,
    ) -> list[ViewSegmentation]:
        """PanoAnalysisCache의 집계값으로부터 엔진 입력 형태 복원.

        엔진은 5-view별 세부값을 요구하지만, 캐시엔 이미 집계된
        SVF/GVI/BVI만 있음. 집계 결과가 같아지도록 역산:
        - up view: sky_ratio=svf
        - 수평 4 view: vegetation_ratio=gvi, building_ratio=bvi (모두 동일)
        """
        up = ViewSegmentation(
            direction="up",
            sky_ratio=analysis.svf,
            vegetation_ratio=0.0,
            building_ratio=0.0,
        )
        horizontals = [
            ViewSegmentation(
                direction=d,
                sky_ratio=0.0,
                vegetation_ratio=analysis.gvi,
                building_ratio=analysis.bvi,
            )
            for d in ("front", "back", "left", "right")
        ]
        return [up] + horizontals

    @staticmethod
    def _build_material_fractions(
        material_ratios: dict[str, float],
    ) -> list[MaterialFraction]:
        """재질 비율 dict → MaterialFraction 리스트."""
        from app.data.material_properties import MATERIAL_DB

        valid_materials = set(MATERIAL_DB.keys())
        fractions = []
        for mat_name, ratio in material_ratios.items():
            if mat_name not in valid_materials:
                mat_name = "unknown"
            if ratio > 0:
                fractions.append(
                    MaterialFraction(material=mat_name, fraction=ratio)
                )
        return fractions or [MaterialFraction(material="unknown", fraction=1.0)]
