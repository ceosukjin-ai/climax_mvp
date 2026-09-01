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
from app.services.kma import (
    ASOS_STATIONS,
    ASOSClient,
    ASOSObservation,
    KMAClient,
    KST,
    latlon_to_grid,
)
from app.services.road_axis import get_road_axis
from app.services.street_view import (
    VIEW_CONFIG,
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

# ===== 하늘상태(SKY) 조회 설정 =====
# 초단기예보의 SKY(1맑음/3구름많음/4흐림)를 운량 감쇠에 연결 —
# 비·흐림 날 "맑음 가정 일사"로 인한 체감온도 과대평가 방지 (2026-08-09).
SKY_TIMEOUT_SEC = 1.5            # 예보 조회 하드 타임아웃
SKY_CACHE_TTL_SEC = 30 * 60      # 예보는 매시 갱신 — 격자당 30분 캐시면 충분
SKY_FAIL_TTL_SEC = 5 * 60        # 실패 시 잠시 재시도 억제 (응답 지연 방지)
_SKY_NAME_TO_CODE = {"맑음": 1, "구름많음": 3, "흐림": 4}
# SKY 코드 → 대표 운량(구간 중앙값, vpti_core config 와 동일) — 앱 표시용
_SKY_CODE_TO_CF = {1: 0.25, 3: 0.70, 4: 0.95}

# ===== ASOS 실측(운량·일사·지면온도) 설정 (2026-08-11) =====
# 관측소 실측이 예보 SKY보다 우선. 관측은 매시 정시 + 발표 ~10여 분 지연 →
# 관측소당 20분 캐시면 시간당 최대 3회 호출(전국 사용자 수와 무관).
ASOS_TIMEOUT_SEC = 1.5
ASOS_CACHE_TTL_SEC = 20 * 60
ASOS_FAIL_TTL_SEC = 5 * 60
ASOS_MAX_DISTANCE_KM = 50.0      # 이보다 먼 관측소의 운량은 국지성 때문에 미사용
ASOS_MAX_AGE_SEC = 2.5 * 3600    # 관측이 이보다 오래되면 미사용(예보 폴백)

# ===== 거리영상 호출 월 상한 (2026-08-21) =====
# 단위는 **이미지 요청 수**(파노라마 1지점 = 5-view = 5요청).
# 구글 무료 한도가 SKU당 월 1만 요청이므로 9,000에서 먼저 멈춘다 →
#  ① 초과 과금 차단  ② 약관 3.2.3(a)(ii) bulk download 로 읽힐 트래픽 차단.
# 0 이하면 상한 없음(개발용).
IMAGERY_MONTHLY_BUDGET_DEFAULT = 9_000
IMAGERY_BUDGET_WARN_RATIO = 0.9

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
    # 운량 정보 (2026-08-11) — 앱 하늘상태 카드용
    cloud_fraction: float | None = None   # 엔진에 실제 들어간 전운량 [0,1]
    cloud_source: str | None = None       # 실측(일사) | 실측(운량) | 예보 | None(청천 가정)


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
        asos: ASOSClient | None = None,
        aws_obs=None,          # app.services.aws_obs.AWSObsClient — 강수 판정용(선택)
        archive=None,          # app.services.archive.Archive — 측정 이력 적재(선택)
        imagery_monthly_budget: int = IMAGERY_MONTHLY_BUDGET_DEFAULT,
    ) -> None:
        self.cache = cache
        self.street_view = street_view
        self.kma = kma
        self.segformer = segformer
        self.asos = asos
        self.aws_obs = aws_obs
        self.archive = archive
        self.imagery_monthly_budget = imagery_monthly_budget
        # 하늘상태(SKY) 인메모리 캐시: (nx,ny) → (sky_code|None, 만료 monotonic)
        self._sky_cache: dict[tuple[int, int], tuple[int | None, float]] = {}
        # ASOS 실측 인메모리 캐시: station_id → (ASOSObservation|None, 만료 monotonic)
        self._asos_cache: dict[int, tuple[ASOSObservation | None, float]] = {}
        # 좌표 단위 강수 판정 (격자 보간 + 관측 사실 + 접근 판정) — 2026-09-01
        from app.services.rain import RainService
        self.rain = RainService(kma=kma, asos=asos, aws=aws_obs)

    # ===== panoId 해석 =====

    # 파노라마 탐색 반경 단계 [m].
    # 한국은 골목 단위 Street View 커버리지가 빈 곳이 많아(예: 부산 장전동 주택가),
    # 정확히 그 지점(50m)에 없으면 인근 촬영 지점으로 단계적으로 넓혀 찾는다.
    # 같은 블록(≤400m)의 가로 형태는 공간 지표(SVF/GVI/BVI)의 근사로 유효.
    PANO_SEARCH_RADII_M = (50, 150, 400)

    async def _resolve_pano_id(self, lat: float, lon: float) -> tuple[str, float, float]:
        """좌표 → panoId.

        1차: Redis 좌표 매핑
        2차: Google Metadata API(반경 50→150→400m 단계 확장) → 캐시 저장
        """
        cached = await self.cache.get_pano_id_for_location(lat, lon)
        if cached:
            return cached, lat, lon

        meta = None
        for radius_m in self.PANO_SEARCH_RADII_M:
            meta = await self.street_view.get_pano_metadata(lat, lon, radius_m=radius_m)
            if meta.status == "OK" and meta.pano_id:
                if radius_m > self.PANO_SEARCH_RADII_M[0]:
                    logger.info(
                        "Street View: 지점 50m 내 없음 → 반경 {}m에서 인근 파노라마 사용 "
                        "({}, {}) → pano={}",
                        radius_m, lat, lon, meta.pano_id,
                    )
                break

        if meta is None or meta.status != "OK" or not meta.pano_id:
            status = meta.status if meta else "UNKNOWN"
            raise StreetViewNotFound(
                f"이 지점 주변 {self.PANO_SEARCH_RADII_M[-1]}m 안에 거리 이미지가 없어 "
                f"측정할 수 없어요. 큰길 근처에서 다시 시도해 주세요. "
                f"({lat:.5f}, {lon:.5f}: {status})"
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
        # ⚠️ 신규 다운로드 전 월 상한 확인 — 과금 차단 + bulk download 방지.
        await self._check_imagery_budget()
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

    async def _check_imagery_budget(self) -> None:
        """이번 달 거리영상 이미지 요청 상한 확인. 초과면 신규 분석을 거부한다.

        왜 있나 (2026-08-21 법적 리스크 검토):
          ① 구글 무료 한도(SKU당 월 1만 요청)를 넘기면 즉시 과금이다.
          ② 같은 트래픽이 약관 3.2.3(a)(ii)가 금지하는 "bulk download Street View
             images"로 읽힌다. 일괄 스캔 스크립트가 실수로 돌아도 여기서 멈춘다.

        이미 캐시된 지점의 측정은 영향을 받지 않는다(신규 다운로드만 막는다).
        카운터(Redis) 장애 시에는 서비스를 죽이지 않고 그냥 통과시킨다.
        """
        budget = self.imagery_monthly_budget
        if budget <= 0:
            return
        ym = datetime.now(timezone.utc).strftime("%Y%m")
        n_images = len(VIEW_CONFIG)   # 파노라마 1지점 = 5-view
        try:
            count = int(await self.cache.incr_imagery_fetch(ym, n_images))
        except Exception as e:  # noqa: BLE001
            # Redis 장애·미구현 캐시(테스트 목) 모두 여기로 — 서비스를 죽이지 않는다.
            logger.warning("[quota] 거리영상 카운터 실패(계속 진행): {}", e)
            return

        if count > budget:
            logger.error(
                "[quota] {} 거리영상 요청 {}/{} — 상한 초과, 신규 분석 중단",
                ym, count, budget,
            )
            raise StreetViewNotFound(
                "이번 달 거리 이미지 분석 한도에 도달했어요. 이미 분석된 곳은 "
                "그대로 측정되고, 새로운 지점은 다음 달 1일에 다시 열려요."
            )
        if count - n_images < budget * IMAGERY_BUDGET_WARN_RATIO <= count:
            logger.warning(
                "[quota] {} 거리영상 요청 {}/{} — 무료 한도 90% 도달", ym, count, budget
            )

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
            # 이 지표가 어느 영상에서 나왔는지 반드시 남긴다 (2026-08-21).
            # 원천 교체(Mapillary/자체촬영) 시 GSV 유래만 골라 폐기·재계산한다.
            imagery_source=getattr(self.street_view, "IMAGERY_SOURCE", "gsv"),
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

    # ===== 하늘상태(SKY) — 운량 감쇠용 (2026-08-09) =====

    def _sky_from_cache(self, nx: int, ny: int) -> tuple[bool, int | None]:
        """(캐시 존재 여부, sky_code). 만료 항목은 없음으로 취급."""
        hit = self._sky_cache.get((nx, ny))
        if hit is None:
            return False, None
        sky, expires = hit
        if time.monotonic() > expires:
            return False, None
        return True, sky

    async def _get_sky_code(
        self, lat: float, lon: float, weather: WeatherContext
    ) -> int | None:
        """KMA 초단기예보의 SKY를 조회해 일사(운량) 감쇠에 사용.

        - 지금 비가 오면(실황 강수 RN1 > 0) 예보와 무관하게 흐림(4).
        - 격자 단위 30분 인메모리 캐시 — 측정마다 API를 부르지 않음.
        - 조회 실패 시 None(청천 가정, 기존 동작 그대로) + 5분 재시도 억제.
        """
        grid = latlon_to_grid(lat, lon)
        raining = weather.precipitation_mm > 0.0

        found, cached_sky = self._sky_from_cache(grid.nx, grid.ny)
        if found:
            return 4 if raining else cached_sky

        sky: int | None = None
        ttl = SKY_FAIL_TTL_SEC
        try:
            forecasts = await asyncio.wait_for(
                self.kma.get_ultra_short_forecast(lat, lon),
                timeout=SKY_TIMEOUT_SEC,
            )
            if forecasts:
                f = forecasts[0]  # 가장 가까운 예보 시각
                sky = _SKY_NAME_TO_CODE.get(f.sky_condition or "")
                # 예보상 강수(비/눈/소나기 등)면 흐림으로 강화
                if f.precipitation_type not in (None, "없음"):
                    sky = 4
                ttl = SKY_CACHE_TTL_SEC
                logger.info(
                    "[sky] grid {},{} → SKY={} (예보 {}, 강수형태 {})",
                    grid.nx, grid.ny, sky, f.sky_condition, f.precipitation_type,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("[sky] 초단기예보 조회 실패 → 청천 가정: {}", e)

        self._sky_cache[(grid.nx, grid.ny)] = (sky, time.monotonic() + ttl)
        return 4 if raining else sky

    # ===== ASOS 실측 운량·일사 (2026-08-11) =====

    async def _get_asos_obs(self, station_id: int) -> ASOSObservation | None:
        """관측소 최신 실측 1건 — 20분 인메모리 캐시, 실패 시 5분 재시도 억제."""
        hit = self._asos_cache.get(station_id)
        if hit is not None:
            obs, expires = hit
            if time.monotonic() <= expires:
                return obs

        obs: ASOSObservation | None = None
        ttl = ASOS_FAIL_TTL_SEC
        try:
            obs = await asyncio.wait_for(
                self.asos.get_hourly(station_id), timeout=ASOS_TIMEOUT_SEC
            )
            if obs is not None:
                ttl = ASOS_CACHE_TTL_SEC
                # 지면온도(TS)는 엔진 Tsurf(에너지수지) 상시 교정용 — 로그로 축적
                logger.info(
                    "[asos] stn={} tm={} ta={} hm={} ws={} 전운량={}/10 일사={}MJ 지면온도={}°C",
                    station_id, obs.observed_at.strftime("%H:%M"),
                    obs.temperature_c, obs.humidity_pct, obs.wind_speed_ms,
                    obs.cloud_cover_tenths, obs.solar_mj, obs.ground_temp_c,
                )
                # 실측 ↔ 엔진 추정 짝 기록 — 로그는 재배포하면 사라지므로 DB에 남긴다.
                #
                # 2026-08-15: 스키마의 est_ground_c 가 비어 있던 것을 채운다.
                # 같은 시각·같은 조건으로 엔진 에너지수지를 돌려 **추정 지면온도**를
                # 함께 적재 → 실측(TS)과의 잔차가 쌓이면 f_stor·h_c 계수를 데이터로
                # 교정할 수 있다 (7/17 문서 '남은 정밀화' 항목의 실행).
                # ASOS 관측소는 개활지(잔디/나지) — SVF=1, GVI=0, 알베도 0.20 근사가
                # 가장 깨끗한 비교 조건: 도시 혼합 없이 순수하게 수지식만 검증된다.
                if self.archive is not None:
                    est_ground: float | None = None
                    est_cf: float | None = None
                    if obs.temperature_c is not None:
                        try:
                            from vpti_core.mrt import estimate_ground_temp, sky_emissivity
                            from vpti_core.solar import estimate_solar

                            slat, slon = ASOS_STATIONS[station_id]
                            est_cf, _src = self._asos_cloud_fraction(obs, station_id)
                            solar_now = estimate_solar(
                                slat, slon, obs.observed_at, cloud_fraction=est_cf,
                            )
                            eps_sky = sky_emissivity(
                                obs.temperature_c, obs.humidity_pct or 50.0,
                                solar_now.cloud_fraction,
                            )
                            est_ground = estimate_ground_temp(
                                air_temp_c=obs.temperature_c,
                                solar=solar_now,
                                ground_albedo=0.20,      # 관측소 잔디/나지 근사
                                ground_emissivity=0.95,
                                svf=1.0, gvi=0.0,        # 개활지
                                wind_ms=obs.wind_speed_ms or 0.5,
                                eps_sky=eps_sky,
                            )
                            if obs.ground_temp_c is not None:
                                logger.info(
                                    "[tsurf-calib] stn={} 실측 {}°C vs 추정 {:.1f}°C"
                                    " (잔차 {:+.1f})",
                                    station_id, obs.ground_temp_c, est_ground,
                                    est_ground - obs.ground_temp_c,
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(
                                "[tsurf-calib] 추정 실패 ({}): {}", type(e).__name__, e
                            )
                    self.archive.record_engine_check(
                        observed_at=obs.observed_at,
                        station_id=station_id,
                        obs_ground_c=obs.ground_temp_c,
                        est_ground_c=(round(est_ground, 2)
                                      if est_ground is not None else None),
                        obs_solar_mj=obs.solar_mj,
                        obs_cloud=(obs.cloud_cover_tenths / 10.0
                                   if obs.cloud_cover_tenths is not None else None),
                        est_cloud=(round(est_cf, 3) if est_cf is not None else None),
                        air_temp=obs.temperature_c,
                        wind_ms=obs.wind_speed_ms,
                        note="ASOS 정시 관측",
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("[asos] stn={} 조회 실패 → SKY 예보 폴백: {}", station_id, e)

        self._asos_cache[station_id] = (obs, time.monotonic() + ttl)
        return obs

    def _asos_cloud_fraction(
        self, obs: ASOSObservation, station_id: int
    ) -> tuple[float | None, str | None]:
        """실측 → (전운량 비율, 출처). 주간엔 일사 역산(감쇠 실측)이 전운량보다 우선."""
        slat, slon = ASOS_STATIONS[station_id]
        cf: float | None = None
        source: str | None = None
        ghi_obs = obs.solar_avg_wm2
        if ghi_obs is not None:
            from vpti_core.solar import cloud_fraction_from_obs_ghi

            cf = cloud_fraction_from_obs_ghi(slat, slon, obs.observed_at, ghi_obs)
            if cf is not None:
                source = "실측(일사)"
        if cf is None and obs.cloud_cover_tenths is not None:
            cf = obs.cloud_cover_tenths / 10.0
            source = "실측(운량)"
        if cf is not None:
            logger.info("[asos] cloud_fraction={:.2f} ({})", cf, source)
        return cf, source

    async def _get_cloud_fraction(
        self, lat: float, lon: float, weather: WeatherContext
    ) -> tuple[float | None, int | None, str | None]:
        """운량 결정 — 우선순위: ① ASOS 실측(50km 내 관측소) ② SKY 예보 ③ None(청천).

        Returns:
            (cloud_fraction, sky_code, source) — cloud_fraction 이 있으면 엔진에서
            우선 사용, None 이면 sky_code(예보) 경로. 비가 오면 흐림 하한(0.95).
        """
        raining = weather.precipitation_mm > 0.0
        cf: float | None = None
        source: str | None = None

        if self.asos is not None:
            station_id = ASOSClient.nearest_station(lat, lon, ASOS_MAX_DISTANCE_KM)
            if station_id is not None:
                obs = await self._get_asos_obs(station_id)
                if obs is not None:
                    age = (datetime.now(KST) - obs.observed_at).total_seconds()
                    if age <= ASOS_MAX_AGE_SEC:
                        cf, source = self._asos_cloud_fraction(obs, station_id)
                    else:
                        logger.warning(
                            "[asos] 관측 {}분 경과(오래됨) → SKY 예보 폴백", int(age / 60)
                        )

        if cf is not None:
            if raining:
                cf = max(cf, 0.95)
            return cf, None, source

        sky_code = await self._get_sky_code(lat, lon, weather)
        return None, sky_code, ("예보" if sky_code is not None else None)

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
        archive_consent: bool = False,
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

        # 운량 — ① ASOS 실측(일사 역산>전운량) ② SKY 예보 ③ 청천 가정 (2026-08-11)
        cloud_fraction, sky_code, cloud_source = await self._get_cloud_fraction(
            clat, clon, weather
        )

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

        # 직달일사 태양방향 차폐 (2026-08-16) — SVF는 등방이라 "태양이 저 건물
        # 뒤인가"를 못 본다. 건물 폴리곤(V-World/OSM, 30일 캐시) + 태양 방위로
        # 판정해, 그늘이면 직달(DNI)만 차단한다(산란·장파는 SVF가 이미 처리).
        # 실패·타임아웃(2초)이면 1.0 = 기존과 동일 — 서비스에 영향 없음.
        direct_shade = 1.0
        shade_note: str | None = None
        try:
            from vpti_core.solar import estimate_solar
            from app.services.geo import sun_blocked_outdoor

            sun = estimate_solar(clat, clon, when, sky_code=sky_code,
                                 cloud_fraction=cloud_fraction)
            if sun.is_daytime:
                blocked, shade_note = await asyncio.wait_for(
                    sun_blocked_outdoor(
                        clat, clon,
                        sun.solar_azimuth_deg, sun.solar_elevation_deg,
                    ),
                    timeout=2.0,
                )
                if blocked:
                    direct_shade = 0.0
                    logger.info("[shade] {} — 직달 차단", shade_note)
        except Exception as e:  # noqa: BLE001
            logger.warning("[shade] 판정 실패({}) → 미차폐 가정", type(e).__name__)

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
            sky_code=sky_code,               # 예보 SKY(폴백 경로)
            cloud_fraction=cloud_fraction,   # ASOS 실측 운량 — 있으면 SKY보다 우선
            direct_shade=direct_shade,       # 태양방향 건물 차폐 (그늘이면 0.0)
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
            cloud_fraction=(
                cloud_fraction
                if cloud_fraction is not None
                else _SKY_CODE_TO_CF.get(sky_code or 0)
            ),
            cloud_source=cloud_source,
        )
        # 측정 이력 적재 — 개인 식별자 없이 '이 자리가 몇 도였는가'만 남긴다.
        # ⚠️ 이용자가 동의한 경우에만 적재한다(옵트인). 동의 없이 쌓으면 개인정보 처리방침 위반.
        if self.archive is not None and archive_consent:
            # 개인화 전 값(base_*)을 남긴다 — 장소의 특성이지 사람의 특성이 아니어야
            # 여러 사용자의 측정을 한 격자에서 비교·집계할 수 있다.
            inputs = getattr(getattr(result, "comfort", None), "inputs", None)
            self.archive.record_measurement(
                observed_at=when,
                lat=clat, lon=clon,
                pvpti=getattr(result, "base_vpti", None),
                risk_level=getattr(result, "base_risk_level", None),
                air_temp=weather.temperature_c,
                humidity=weather.humidity_pct,
                wind_ms=weather.wind_speed_ms,
                mrt=getattr(inputs, "tr", None),
                svf=pano_analysis.svf, gvi=pano_analysis.gvi, bvi=pano_analysis.bvi,
                cloud=(cloud_fraction if cloud_fraction is not None
                       else _SKY_CODE_TO_CF.get(sky_code or 0)),
                cloud_src=cloud_source,
                indoor=False,
                source="app",
                # 공간지표(svf/gvi/bvi)의 영상 출처 — ML 학습 대상 선별에 쓴다.
                imagery_src=pano_analysis.imagery_source,
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

        # 운량 — peek은 네트워크 금지: ASOS·SKY 인메모리 캐시만 사용, 지금 비면 흐림
        peek_cf: float | None = None
        if self.asos is not None:
            stn = ASOSClient.nearest_station(lat, lon, ASOS_MAX_DISTANCE_KM)
            if stn is not None:
                hit = self._asos_cache.get(stn)
                if hit is not None and time.monotonic() <= hit[1] and hit[0] is not None:
                    peek_cf = self._asos_cloud_fraction(hit[0], stn)
        _, peek_sky = self._sky_from_cache(grid.nx, grid.ny)
        if (wcache.precipitation_mm or 0.0) > 0.0:
            peek_sky = 4
            if peek_cf is not None:
                peek_cf = max(peek_cf, 0.95)

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
            sky_code=peek_sky,
            cloud_fraction=peek_cf,
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
        """현재 강수 + 0~6시간 강수 전망 (2026-09-01 좌표 단위로 개선).

        예전에는 최근접 격자 하나의 초단기예보를 그대로 썼다. 이제 app.services.rain
        이 인접 격자를 보간하고, '지금 비가 오나'는 관측(초단기실황 + 주변 관측소 실측)
        으로 답하며, 바람 불어오는 쪽에 비가 있으면 도착시간 범위를 함께 준다.

        **기존 응답 키는 하나도 빼지 않았다** — 구버전 앱은 새 필드를 무시하면 그만이다.
        rain 파이프라인이 실패하면 예전 방식(최근접 격자 초단기예보)으로 되돌아간다.
        """
        from app.services import rain as rain_mod

        try:
            r = await self.rain.rain_at(lat, lon)
        except Exception as e:  # noqa: BLE001
            logger.warning("좌표 강수 판정 실패 — 최근접 격자 예보로 폴백: {}", e)
            return await self._legacy_precipitation_outlook(lat, lon)

        payload = rain_mod.to_dict(r)
        onset_hours = None
        if r.onset_at is not None:
            onset_hours = max(0, round(
                (r.onset_at - datetime.now(KST)).total_seconds() / 3600
            ))
        max_precip = max(
            [r.now_precip_mm] + [h.precip_mm for h in r.timeline] or [0.0]
        )

        # 구버전 앱은 umbrella_recommended 만 보고 "우산 챙기세요"를 띄운다.
        # 그래서 이 값은 **실제로 우산이 필요한 경우**로만 켠다.
        #   · 지금 비가 온다  · 6시간 안에 비 예보가 있다
        # 빗방울이나 먼 곳의 비로는 켜지 않는다 — 2026-09-01 운영에서 120km 밖 비로
        # 우산을 권하고 있었다.
        umbrella = (r.level == "비") or (r.onset_at is not None)
        rain_expected = (
            umbrella
            or r.level == "빗방울"
            or (r.approaching is not None and r.eta_min_range is not None)
        )

        # --- 구버전 앱이 읽는 키 (형태·의미 그대로 유지) ---
        payload.update({
            "rain_expected_6h": rain_expected,
            "onset_in_hours": onset_hours,
            "max_precip_mm": round(max_precip, 1),
            "umbrella_recommended": umbrella,
        })
        return payload

    async def _legacy_precipitation_outlook(self, lat: float, lon: float) -> dict:
        """폴백 — 2026-09-01 이전 방식(최근접 격자 초단기예보)."""
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
            "umbrella_recommended": rain_expected,
            "advice": advice,
            "hourly": hourly,
            "source": "최근접격자(폴백)",
            "confidence": "낮음",
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
