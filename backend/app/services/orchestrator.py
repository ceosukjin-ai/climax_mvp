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
import time
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

# vpti_core PET+PHI 경로 — pVPTI 자동 산출용. app.core(휴리스틱)와 별개.
from vpti_core import (
    MATERIAL_DB as CORE_MATERIAL_DB,
    Biometrics,
    MaterialFraction as CoreMaterialFraction,
    PersonalizedVPTIResult,
    PhysiologyProfile,
    ViewSegmentation as CoreViewSegmentation,
    WeatherContext as CoreWeatherContext,
    compute_pvpti,
)
from app.services.kma import KMAClient, KST, latlon_to_grid
from app.services.road_axis import get_road_axis
from app.services.street_view import (
    GoogleStreetViewClient,
    StreetViewFetchResult,
    StreetViewNotFound,
)

if TYPE_CHECKING:
    from app.ml.segformer import SegFormerService


# KMA 기상 조회 하드 타임아웃 [초]. Overpass 와 같은 패턴 — 초과 시 폴백(캐시→재시도→기본값).
KMA_TIMEOUT_SEC = 1.5
KMA_RETRY_TIMEOUT_SEC = 1.0

# KMA 완전 실패 시 안전 기본값(추정). SMTI/PET 입력용 온화한 중립값.
_DEFAULT_WEATHER = dict(
    temperature_c=22.0, humidity_pct=60.0,
    wind_speed_ms=1.5, wind_direction_deg=0.0, precipitation_mm=0.0,
)

# prefetch(앞 미리 분석): 진행 방향 앞 지점을 백그라운드로 미리 캐시해 도착 시 hit.
PREFETCH_DISTANCES_M = (25.0, 50.0)   # 앞 2지점만 — 2코어 서버 부담 보호
PREFETCH_HORIZON_SEC = 15.0           # speed_kmh 기준 이 시간 내 도달 거리까지만
_EARTH_RADIUS_M = 6_371_000.0


def destination_point(
    lat: float, lon: float, bearing_deg: float, dist_m: float
) -> tuple[float, float]:
    """(lat,lon)에서 bearing 방향으로 dist_m 앞 좌표(구면 순방향 측지, 하버사인 역산)."""
    import math

    br = math.radians(bearing_deg)
    dr = dist_m / _EARTH_RADIUS_M
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(dr) + math.cos(lat1) * math.sin(dr) * math.cos(br)
    )
    lon2 = lon1 + math.atan2(
        math.sin(br) * math.sin(dr) * math.cos(lat1),
        math.cos(dr) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


@dataclass(frozen=True, slots=True)
class PipelineTelemetry:
    """요청별 성능 추적. WebSocket에서 frontend에 보내 모니터링."""

    pano_cache_hit: bool
    weather_cache_hit: bool
    total_ms: float
    street_view_ms: float
    segmentation_ms: float
    weather_ms: float
    weather_source: str = "실측"   # 실측 | 캐시 | 추정


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

        sv_start = time.perf_counter()
        sv_result = await self._fetch_with_metadata(pano_id, lat, lon)
        sv_ms = (time.perf_counter() - sv_start) * 1000
        logger.info("[timing] Street View 5-view 다운로드: {:.0f}ms (pano={})", sv_ms, pano_id)

        seg_start = time.perf_counter()
        analysis = await self._analyze_views(sv_result)
        seg_ms = (time.perf_counter() - seg_start) * 1000
        logger.info("[timing] SegFormer 추론(5-view)+도로축: {:.0f}ms", seg_ms)

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
        """5-view → 세그멘테이션 → SVF/GVI/BVI/재질비율 + 도로축.

        SegFormer 추론은 CPU-bound이므로 to_thread로 이벤트루프에서 분리.
        도로축(get_road_axis, Overpass 네트워크)은 세그멘테이션과 **동시** 실행하고,
        결과를 panoId 캐시에 함께 넣는다 → miss 때 1회만 계산, 재방문은 캐시 hit(네트워크 X).
        """
        # 5개 방향 동시 추론(개별 to_thread) — CPU 멀티코어에 분산돼 배치보다 빠름.
        # (segment_batch 는 GPU 배치 이득용으로 SegFormerService 에 남겨둠. CPU 에선 미사용.)
        seg_tasks = [
            asyncio.to_thread(self.segformer.segment, img_bytes)
            for img_bytes in sv_result.images.values()
        ]

        # SegFormer 추론과 도로축(Overpass)은 동시 실행 — 각각 따로 계측(진단용).
        async def _timed_seg() -> list:
            t = time.perf_counter()
            segs = await asyncio.gather(*seg_tasks)
            logger.info("[timing]   └ SegFormer 5-view 추론(개별 동시): {:.0f}ms", (time.perf_counter() - t) * 1000)
            return segs

        async def _timed_road():
            t = time.perf_counter()
            r = await get_road_axis(sv_result.lat, sv_result.lon)
            logger.info("[timing]   └ 도로축(Overpass): {:.0f}ms", (time.perf_counter() - t) * 1000)
            return r

        segmentations, road = await asyncio.gather(_timed_seg(), _timed_road())
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
            road_axis_deg=road.road_axis_deg,
            road_axis_source=road.source,
        )

    # ===== 기상 조회 =====

    @staticmethod
    def _wc_from_cache(c: WeatherCache) -> WeatherContext:
        return WeatherContext(
            temperature_c=c.temperature_c,
            humidity_pct=c.humidity_pct,
            wind_speed_ms=c.wind_speed_ms,
            wind_direction_deg=c.wind_direction_deg,
            precipitation_mm=c.precipitation_mm,
        )

    async def _get_weather(
        self, lat: float, lon: float
    ) -> tuple[WeatherContext, bool, float, str]:
        """기상 조회. 격자 단위 캐싱 + 하드 타임아웃 폴백.

        폴백(타임아웃/오류 시): ① 마지막 정상값 재사용(캐시) → ② 짧은 재시도 →
        ③ 안전 기본값(추정). weather_source 로 실측/캐시/추정 구분.

        Returns:
            (weather, is_cache_hit, elapsed_ms, weather_source)
        """
        grid = latlon_to_grid(lat, lon)

        # 10분 신선 캐시 hit — 실측 데이터를 캐시한 것.
        cached = await self.cache.get_weather(grid.nx, grid.ny)
        if cached is not None:
            return (self._wc_from_cache(cached), True, 0.0, "실측")

        start = time.perf_counter()
        obs = None
        try:
            obs = await asyncio.wait_for(
                self.kma.get_current_observation(lat, lon), timeout=KMA_TIMEOUT_SEC
            )
        except Exception as e:  # noqa: BLE001  (TimeoutError 포함)
            elapsed_ms = (time.perf_counter() - start) * 1000
            reason = f"{KMA_TIMEOUT_SEC:.1f}s 타임아웃" if isinstance(e, asyncio.TimeoutError) else str(e)
            logger.warning("[timing] 기상(KMA) 1차 실패({:.0f}ms): {}", elapsed_ms, reason)

            # ① 마지막 정상값(장기 캐시) 재사용
            last = await self.cache.get_weather_last_good(grid.nx, grid.ny)
            if last is not None:
                logger.warning("[timing] 기상 → 마지막 정상값(캐시) 재사용")
                return (self._wc_from_cache(last), False, elapsed_ms, "캐시")

            # ② 짧은 재시도
            try:
                obs = await asyncio.wait_for(
                    self.kma.get_current_observation(lat, lon),
                    timeout=KMA_RETRY_TIMEOUT_SEC,
                )
            except Exception:  # noqa: BLE001
                obs = None

            # ③ 안전 기본값(추정)
            if obs is None:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.warning("[timing] 기상 → 안전 기본값(추정) 사용")
                return (WeatherContext(**_DEFAULT_WEATHER), False, elapsed_ms, "추정")

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("[timing] 기상(KMA) 조회: {:.0f}ms (grid {},{})", elapsed_ms, grid.nx, grid.ny)

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
        await self.cache.set_weather(weather_cache)             # 10분 신선 캐시
        await self.cache.set_weather_last_good(weather_cache)   # 6시간 폴백용

        return (self._wc_from_cache(weather_cache), False, elapsed_ms, "실측")

    # ===== 메인 파이프라인 =====

    async def compute(
        self,
        lat: float,
        lon: float,
        timestamp: datetime | None = None,
    ) -> tuple[VPTIResult, PipelineTelemetry]:
        """전체 파이프라인 실행."""
        loop_start = time.perf_counter()

        # 1. panoId 해석 (좌표→panoId, 캐시 miss 시 Google Metadata API)
        t_resolve = time.perf_counter()
        pano_id, canonical_lat, canonical_lon = await self._resolve_pano_id(lat, lon)
        resolve_ms = (time.perf_counter() - t_resolve) * 1000

        # 2 & 3 병렬 실행: 공간 분석 + 기상 조회
        pano_task = self._get_or_compute_pano_analysis(
            pano_id, canonical_lat, canonical_lon
        )
        weather_task = self._get_weather(canonical_lat, canonical_lon)

        (pano_analysis, pano_hit, sv_ms, seg_ms), (
            weather,
            weather_hit,
            weather_ms,
            weather_source,
        ) = await asyncio.gather(pano_task, weather_task)

        # 4. VPTI 산출
        # 공간 분석 결과를 엔진이 기대하는 형태로 변환
        views_5 = self._build_synthetic_views(pano_analysis)
        materials = self._build_material_fractions(pano_analysis.material_ratios)

        t_idx = time.perf_counter()
        vpti_result = compute_vpti(
            views_5=views_5,
            materials=materials,
            weather=weather,
            latitude=canonical_lat,
            longitude=canonical_lon,
            timestamp=timestamp,
        )
        index_ms = (time.perf_counter() - t_idx) * 1000

        total_ms = (time.perf_counter() - loop_start) * 1000
        telemetry = PipelineTelemetry(
            pano_cache_hit=pano_hit,
            weather_cache_hit=weather_hit,
            total_ms=total_ms,
            street_view_ms=sv_ms,
            segmentation_ms=seg_ms,
            weather_ms=weather_ms,
            weather_source=weather_source,
        )

        logger.info(
            "[timing] VPTI {} | pano_hit={} weather_hit={} wsrc={} | resolve={:.0f} sv={:.0f} seg={:.0f} weather={:.0f} index(VSI/SMTI/PWI)={:.1f} | total={:.0f}ms",
            "cached" if pano_hit and weather_hit else "computed",
            pano_hit, weather_hit, weather_source,
            resolve_ms, sv_ms, seg_ms, weather_ms, index_ms, total_ms,
        )

        return vpti_result, telemetry

    # ===== 자동 pVPTI 파이프라인 (생리 개인화, vpti_core PET 경로) =====

    async def compute_personalized(
        self,
        lat: float,
        lon: float,
        bio: Biometrics,
        profile: PhysiologyProfile | None = None,
        timestamp: datetime | None = None,
    ) -> tuple[PersonalizedVPTIResult, PipelineTelemetry]:
        """좌표 + 애플워치 생체신호만으로 pVPTI 자동 산출.

        B1(/vpti/personalized, 수동 입력)을 orchestrator 자동화로 대체한다:
        좌표 → panoId 공간분석(영구 캐시) + 기상(10분 캐시) → vpti_core PET+PHI.

        캐시 불변성 유지: compute()와 **같은** 분리 캐시(_get_or_compute_pano_analysis,
        _get_weather)를 그대로 재사용한다. 공간·기상을 합치지 않으므로 "재방문 <100ms"가
        유지된다. 새 캐시 키를 추가하지 않는다.

        도로축(road_axis_deg)은 panoId 공간분석 캐시에 함께 저장된다(_analyze_views 에서
        miss 시 1회 계산). 따라서 hot path(캐시 hit)는 Overpass 네트워크를 타지 않는다.
        """
        loop_start = time.perf_counter()

        t_resolve = time.perf_counter()
        pano_id, clat, clon = await self._resolve_pano_id(lat, lon)
        resolve_ms = (time.perf_counter() - t_resolve) * 1000

        pano_task = self._get_or_compute_pano_analysis(pano_id, clat, clon)
        weather_task = self._get_weather(clat, clon)
        (pano_analysis, pano_hit, sv_ms, seg_ms), (
            weather,
            weather_hit,
            weather_ms,
            weather_source,
        ) = await asyncio.gather(pano_task, weather_task)

        # app.core 집계값 → vpti_core 입력 형태로 변환
        views_5 = self._build_core_views(pano_analysis)
        materials = self._build_core_materials(pano_analysis.material_ratios)
        core_weather = CoreWeatherContext(
            temperature_c=weather.temperature_c,
            wind_speed_ms=weather.wind_speed_ms,
            wind_direction_deg=weather.wind_direction_deg,
            humidity_pct=weather.humidity_pct,
        )
        when = timestamp or datetime.now(timezone.utc)

        t_idx = time.perf_counter()
        result = compute_pvpti(
            bio=bio,
            profile=profile,
            views_5=views_5,
            materials=materials,
            weather=core_weather,
            road_axis_deg=pano_analysis.road_axis_deg,   # panoId 캐시의 도로축(OSM/GPS/가정)
            lat=clat,
            lon=clon,
            when=when,
            sky_code=None,       # KMA 실황엔 SKY 없음 → 청천 가정(추후 초단기예보 연계)
        )
        index_ms = (time.perf_counter() - t_idx) * 1000

        total_ms = (time.perf_counter() - loop_start) * 1000
        telemetry = PipelineTelemetry(
            pano_cache_hit=pano_hit,
            weather_cache_hit=weather_hit,
            total_ms=total_ms,
            street_view_ms=sv_ms,
            segmentation_ms=seg_ms,
            weather_ms=weather_ms,
            weather_source=weather_source,
        )
        logger.info(
            "[timing] pVPTI {} | pano_hit={} weather_hit={} wsrc={} road={} | resolve={:.0f} sv={:.0f} seg={:.0f} weather={:.0f} index(VSI/SMTI/PWI+PET)={:.1f} | total={:.0f}ms",
            "cached" if pano_hit and weather_hit else "computed",
            pano_hit, weather_hit, weather_source, pano_analysis.road_axis_source,
            resolve_ms, sv_ms, seg_ms, weather_ms, index_ms, total_ms,
        )
        return result, telemetry

    # ===== 캐시 전용 peek (lookahead 용) =====

    async def peek_personalized(
        self,
        lat: float,
        lon: float,
        bio: Biometrics,
        profile: PhysiologyProfile | None = None,
        timestamp: datetime | None = None,
    ) -> PersonalizedVPTIResult | None:
        """앞 지점이 **이미 캐시(prefetch)** 돼 있으면 pVPTI 산출, 없으면 None.

        콜드 계산(Street View fetch/SegFormer/도로축 네트워크)은 **절대 하지 않는다** →
        본 응답을 막지 않는다. 캐시된 공간분석·기상 + 본 요청 생체신호로 compute_pvpti
        를 그대로 호출(=본 계산과 동일 파이프라인, 새 계산식 없음).
        """
        pano_id = await self.cache.get_pano_id_for_location(lat, lon)
        if pano_id is None:
            return None
        analysis = await self.cache.get_pano_analysis(pano_id)
        if analysis is None:
            return None
        grid = latlon_to_grid(lat, lon)
        wcache = await self.cache.get_weather(grid.nx, grid.ny)
        if wcache is None:
            wcache = await self.cache.get_weather_last_good(grid.nx, grid.ny)
        if wcache is None:
            return None

        core_weather = CoreWeatherContext(
            temperature_c=wcache.temperature_c,
            wind_speed_ms=wcache.wind_speed_ms,
            wind_direction_deg=wcache.wind_direction_deg,
            humidity_pct=wcache.humidity_pct,
        )
        views_5 = self._build_core_views(analysis)
        materials = self._build_core_materials(analysis.material_ratios)
        when = timestamp or datetime.now(timezone.utc)
        return compute_pvpti(
            bio=bio,
            profile=profile,
            views_5=views_5,
            materials=materials,
            weather=core_weather,
            road_axis_deg=analysis.road_axis_deg,
            lat=lat,
            lon=lon,
            when=when,
            sky_code=None,
        )

    # ===== prefetch (앞 미리 분석) =====

    async def prefetch_ahead(
        self,
        lat: float,
        lon: float,
        heading: float,
        speed_kmh: float | None = None,
    ) -> None:
        """진행 방향 앞 지점(25m,50m)을 미리 분석해 캐시에 채운다(백그라운드).

        compute()를 그대로 호출 → 좌표→panoId·공간분석(SegFormer)·기상 캐시를 채운다.
        이미 캐시면 compute()가 hit로 즉시 끝나 재계산하지 않는다(=skip). 계산 방식은
        본 요청과 동일하므로 값 정확도 불변. 실패는 조용히 무시(본 응답에 영향 없음).

        speed_kmh 가 있으면 앞으로 PREFETCH_HORIZON_SEC 초 내 도달 거리까지만 미리 계산
        (느린 이동 시 과도한 prefetch 방지).
        """
        horizon_m = (
            (speed_kmh / 3.6) * PREFETCH_HORIZON_SEC
            if speed_kmh and speed_kmh > 0
            else float("inf")
        )
        warmed: list[tuple[float, str]] = []
        for dist in PREFETCH_DISTANCES_M:
            if dist > horizon_m:
                continue
            alat, alon = destination_point(lat, lon, heading, dist)
            try:
                _, tel = await self.compute(alat, alon)
                warmed.append((dist, "hit" if tel.pano_cache_hit else "computed"))
            except Exception as e:  # noqa: BLE001  (StreetViewNotFound 등)
                logger.debug("[prefetch] {:.0f}m 실패(무시): {}", dist, e)
        if warmed:
            logger.info(
                "[prefetch] heading={:.0f}° speed={}km/h → {}",
                heading, speed_kmh if speed_kmh is not None else "?", warmed,
            )

    @staticmethod
    def _build_core_views(
        analysis: PanoAnalysisCache,
    ) -> list[CoreViewSegmentation]:
        """PanoAnalysisCache 집계값 → vpti_core ViewSegmentation 5개.

        _build_synthetic_views 와 동일한 역산이나, vpti_core 타입으로 만든다
        (vpti_core.ViewSegmentation 은 ground_ratio 를 받지 않음).
        """
        up = CoreViewSegmentation(
            direction="up",
            sky_ratio=analysis.svf,
            vegetation_ratio=0.0,
            building_ratio=0.0,
        )
        horizontals = [
            CoreViewSegmentation(
                direction=d,
                sky_ratio=0.0,
                vegetation_ratio=analysis.gvi,
                building_ratio=analysis.bvi,
            )
            for d in ("front", "back", "left", "right")
        ]
        return [up] + horizontals

    @staticmethod
    def _build_core_materials(
        material_ratios: dict[str, float],
    ) -> list[CoreMaterialFraction]:
        """재질 비율 dict → vpti_core MaterialFraction. 미등록 재질은 'unknown'."""
        valid = set(CORE_MATERIAL_DB.keys())
        fractions = [
            CoreMaterialFraction(
                material=name if name in valid else "unknown", fraction=ratio
            )
            for name, ratio in material_ratios.items()
            if ratio > 0
        ]
        return fractions or [CoreMaterialFraction(material="unknown", fraction=1.0)]

    # ===== 강수 컨텍스트 (VPTI와 분리된 외부 예보 레이어) =====

    async def get_precipitation_outlook(self, lat: float, lon: float) -> dict:
        """현재 강수 + 0~6시간 강수 전망.

        비는 거리 기하로 예측 불가 → 순수 KMA 외부 데이터로만 다루고, VPTI 숫자에는
        섞지 않는다(별도 컨텍스트 레이어). 정확도를 위해 단기예보 POP 대신 초단기예보
        (0~6h)를 사용한다.
        """
        # 현재 강수량은 이미 캐시된 실황에서 재사용
        weather, _, _, _ = await self._get_weather(lat, lon)
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
