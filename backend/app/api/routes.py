"""
VPTI REST API 라우트.

엔드포인트:
- POST /api/v1/vsi/components  — SVF/GVI/BVI로 VSI만 계산 (논문 재현용)
- POST /api/v1/vsi              — 5-view 세그멘테이션 → VSI
- POST /api/v1/vpti             — 전체 VPTI 산출 (수동 입력)
- GET  /api/v1/vpti/at          — 좌표만으로 자동 산출 (Street View + 기상 자동)
- GET  /api/v1/cache/stats      — 캐시 상태 확인 (관리자용)
- GET  /api/v1/health           — 헬스체크
"""
from __future__ import annotations

import asyncio
import math as _math
import time as _time

from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from loguru import logger

from app.config import get_settings
from app.core.smti import MaterialFraction
from app.core.vpti import WeatherContext, compute_vpti
from app.core.vsi import (
    ViewSegmentation,
    compute_vsi,
    compute_vsi_from_components,
)
from app.schemas.vpti import (
    AutoPersonalizedVPTIRequest,
    HealthResponse,
    LookaheadItem,
    PersonalizedVPTIRequest,
    PersonalizedVPTIResponse,
    VPTIRequest,
    VPTIResponse,
    VSIComponentsIn,
    VSIResultOut,
    ViewSegmentationIn,
)
from app.services.kma import KST
from app.services.orchestrator import PREFETCH_DISTANCES_M, destination_point
from app.services.road_axis import bearing_deg
from app.services.street_view import StreetViewNotFound


def _resolve_heading(request: Request, lat: float, lon: float,
                     heading: float | None, session_id: str | None) -> float | None:
    """heading 결정: 명시값 우선, 없으면 세션 직전 좌표 → 현재 좌표 방위로 대체.

    세션 키는 session_id(있으면) 또는 client IP. 세션별 직전 좌표를 app.state 에
    메모리 저장(휘발성 휴리스틱). 무한 증가 방지로 상한 초과 시 비운다.
    """
    sessions = getattr(request.app.state, "prefetch_sessions", None)
    if sessions is None:
        sessions = {}
        request.app.state.prefetch_sessions = sessions
    skey = session_id or (request.client.host if request.client else "anon")

    if heading is None:
        prev = sessions.get(skey)
        if prev is not None and prev != (lat, lon):
            heading = bearing_deg(prev[0], prev[1], lat, lon)
    if len(sessions) > 5000:
        sessions.clear()
    sessions[skey] = (lat, lon)
    return heading

# vpti_core PET 경로(특허 충실) — pVPTI 전용. app.core(휴리스틱)와 별개.
from vpti_core import (
    Biometrics,
    MaterialFraction as CoreMaterialFraction,
    PhysiologyProfile,
    ViewSegmentation as CoreViewSegmentation,
    WeatherContext as CoreWeatherContext,
    compute_pvpti,
)

router = APIRouter(prefix="/api/v1", tags=["vpti"])

# 실내 열 기억 (2026-08-16) — (lat, lon, floor) → (시각, 마지막 실내온도).
# 급변 날씨에서 방이 즉시 리셋되는 문제 방지 (indoor.py ⑤ 참조).
_INDOOR_MEMORY: dict[tuple[float, float, int], tuple[float, float]] = {}
# 실내 잔차 (2026-09-23) — (lat, lon, floor) → (시각, 실측−외부부하 지수평균). 워커별 인메모리.
_INDOOR_RESIDUAL: dict[tuple[float, float, int], tuple[float, float]] = {}


def _indoor_note(measured: bool, basis: dict | None) -> str:
    """실내 체감기후 근거 한 줄 (2026-09-24 대표 지시) — 앱은 이 문구를 그대로 보여준다.

    ① 센서 실측 중 ② 센서 끊김(학습 잔차 적용) ③ 센서 기록 없음(외피 열부하만)
    """
    bs = basis or {}
    if measured:
        return "센서 실측 + VPTI 엔진 기반 실내 체감기후예요"
    if bs.get("residual_bias_applied") is not None:
        return "이 집 센서 기록으로 보정한 VPTI 엔진 예측이에요"
    if bs.get("external_load_model") == "BTLI":
        return "VPTI 엔진이 계산한 이 건물 외피 열부하로 예측했어요"
    return "VPTI 엔진이 건물 정보와 기상청 실황으로 예측했어요"


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """기본 헬스체크. 배포·모니터링용."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.post(
    "/vsi/components",
    response_model=VSIResultOut,
    summary="SVF/GVI/BVI로부터 VSI 계산",
)
async def vsi_from_components(payload: VSIComponentsIn) -> VSIResultOut:
    """이미 산출된 SVF/GVI/BVI로 VSI만 계산.

    논문 값 재현, 가중치 비교, 단위 테스트 등에 유용합니다.
    """
    settings = get_settings()
    weights = payload.weights or settings.vsi_weights

    result = compute_vsi_from_components(
        svf=payload.svf,
        gvi=payload.gvi,
        bvi=payload.bvi,
        weights=weights,
    )
    return VSIResultOut(**result.as_dict())


@router.post(
    "/vsi",
    response_model=VSIResultOut,
    summary="5-view 세그멘테이션으로부터 VSI 계산",
)
async def vsi_from_views(
    views: list[ViewSegmentationIn],
) -> VSIResultOut:
    """다방향 시야 영상 세그멘테이션 결과로 VSI 산출.

    세그멘테이션 모델은 호출자가 실행하고 비율만 전달하는 구조.
    Step 2에서 /pano 엔드포인트가 자동 추론 후 호출합니다.
    """
    if len(views) != 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected 5 views, got {len(views)}",
        )

    settings = get_settings()
    seg_list = [
        ViewSegmentation(
            direction=v.direction,
            sky_ratio=v.sky_ratio,
            vegetation_ratio=v.vegetation_ratio,
            building_ratio=v.building_ratio,
            ground_ratio=v.ground_ratio,
        )
        for v in views
    ]

    try:
        result = compute_vsi(seg_list, weights=settings.vsi_weights)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    return VSIResultOut(**result.as_dict())


@router.post(
    "/vpti",
    response_model=VPTIResponse,
    summary="전체 VPTI 산출 (VSI + SMTI + PWI + 기상)",
)
async def vpti(payload: VPTIRequest) -> VPTIResponse:
    """VPTI 통합 산출.

    입력:
    - 위경도 + 5-view 세그멘테이션 + 재질 비율 + 기상
    - 기상이 None이면 (Step 2 이후) 자동 조회

    출력:
    - VPTI 값 + 위험도 + 3지수 전체 + 원인 분해 + 행동 가이드
    """
    settings = get_settings()

    # 기상 필수 (Step 2에서 None일 때 자동 조회 추가 예정)
    if payload.weather is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Automatic weather fetch not yet implemented. "
                "Provide 'weather' field in request."
            ),
        )

    views_5 = [
        ViewSegmentation(
            direction=v.direction,
            sky_ratio=v.sky_ratio,
            vegetation_ratio=v.vegetation_ratio,
            building_ratio=v.building_ratio,
            ground_ratio=v.ground_ratio,
        )
        for v in payload.views
    ]
    materials = [
        MaterialFraction(material=m.material, fraction=m.fraction)
        for m in payload.materials
    ]
    weather = WeatherContext(
        temperature_c=payload.weather.temperature_c,
        humidity_pct=payload.weather.humidity_pct,
        wind_speed_ms=payload.weather.wind_speed_ms,
        wind_direction_deg=payload.weather.wind_direction_deg,
        precipitation_mm=payload.weather.precipitation_mm,
    )
    weights = payload.vsi_weights or settings.vsi_weights

    try:
        result = compute_vpti(
            views_5=views_5,
            materials=materials,
            weather=weather,
            latitude=payload.location.lat,
            longitude=payload.location.lon,
            timestamp=payload.timestamp,
            vsi_weights=weights,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    return VPTIResponse(**result.as_dict())


@router.get(
    "/vpti/at",
    response_model=VPTIResponse,
    summary="좌표만으로 VPTI 자동 산출 (Street View + 기상 자동조회)",
)
async def vpti_at_location(
    request: Request,
    background: BackgroundTasks,
    lat: float = Query(..., ge=-90.0, le=90.0, description="위도 [deg]"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="경도 [deg]"),
    heading: float | None = Query(None, ge=0.0, lt=360.0, description="진행 방향[deg] (prefetch용)"),
    speed_kmh: float | None = Query(None, ge=0.0, le=300.0, description="이동 속도[km/h] (prefetch용)"),
    session_id: str | None = Query(None, max_length=128, description="세션 식별(heading 대체용)"),
) -> VPTIResponse:
    """위경도만 주면 Street View, 기상청, SegFormer 모두 자동 호출.

    캐시 hit 시 <100ms, miss 시 1~3초 소요. heading(또는 세션 직전좌표)이 있으면
    응답 후 진행 방향 앞 지점을 백그라운드 prefetch.
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized. Backend may still be starting.",
        )

    heading = _resolve_heading(request, lat, lon, heading, session_id)

    try:
        result, telemetry = await orchestrator.compute(lat=lat, lon=lon)
    except StreetViewNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline error: {e}",
        ) from e

    # 강수 컨텍스트(별도 레이어, VPTI 무관). 실패해도 본 응답은 유지.
    precipitation = None
    try:
        precipitation = await orchestrator.get_precipitation_outlook(lat, lon)
    except Exception as e:  # noqa: BLE001
        logger.warning("강수 전망 부착 실패(무시): {}", e)

    # 응답 후 백그라운드로 진행 방향 앞 지점 prefetch (heading 있을 때만)
    if heading is not None:
        background.add_task(orchestrator.prefetch_ahead, lat, lon, heading, speed_kmh)

    response = VPTIResponse(
        **result.as_dict(),
        weather_source=telemetry.weather_source,
        precipitation=precipitation,
    )
    return response


@router.post(
    "/vpti/personalized",
    response_model=PersonalizedVPTIResponse,
    summary="생리 개인화 pVPTI 산출 (애플워치 생체신호 반영, vpti_core PET 경로)",
)
async def vpti_personalized(
    payload: PersonalizedVPTIRequest,
) -> PersonalizedVPTIResponse:
    """생체신호(심박·활동·휴식심박) + 프로필 → pVPTI.

    - app.core(휴리스틱)가 아닌 vpti_core PET 경로를 쓰는 첫 엔드포인트.
    - activity → met 로 PET 개인화, 잔차 심박부하만 위험경계에 반영.
    - ⚠️ 프라이버시: biometrics 는 계산에만 쓰고 저장·로깅하지 않는다(계산 후 폐기).
    """
    views_5 = [
        CoreViewSegmentation(
            direction=v.direction,
            sky_ratio=v.sky_ratio,
            vegetation_ratio=v.vegetation_ratio,
            building_ratio=v.building_ratio,
        )
        for v in payload.views
    ]
    materials = [
        CoreMaterialFraction(material=m.material, fraction=m.fraction)
        for m in payload.materials
    ]
    weather = CoreWeatherContext(
        temperature_c=payload.weather.temperature_c,
        wind_speed_ms=payload.weather.wind_speed_ms,
        wind_direction_deg=payload.weather.wind_direction_deg,
        humidity_pct=payload.weather.humidity_pct,
    )
    bio = Biometrics(
        hr=payload.biometrics.hr,
        activity=payload.biometrics.activity,
        hr_rest=payload.biometrics.hr_rest,
        hr_max=payload.biometrics.hr_max,
    )
    profile = None
    if payload.profile is not None:
        profile = PhysiologyProfile(
            age=payload.profile.age,
            sex=payload.profile.sex,
            height_cm=payload.profile.height_cm,
            weight_kg=payload.profile.weight_kg,
            observed_hr_max=payload.profile.observed_hr_max,
            conditions=tuple(payload.profile.conditions or ()),
        )
    when = payload.timestamp or datetime.now(timezone.utc)

    try:
        result = compute_pvpti(
            bio=bio,
            profile=profile,
            views_5=views_5,
            materials=materials,
            weather=weather,
            road_axis_deg=payload.road_axis_deg,
            lat=payload.location.lat,
            lon=payload.location.lon,
            when=when,
            sky_code=payload.sky_code,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    # as_dict() 키가 응답 필드와 1:1 (biometrics 원본은 반환·로깅하지 않음)
    return PersonalizedVPTIResponse(**result.as_dict())


@router.post(
    "/vpti/personalized/at",
    response_model=PersonalizedVPTIResponse,
    summary="자동 pVPTI (좌표+생체신호 → Street View·기상 자동, vpti_core PET 경로)",
)
async def vpti_personalized_at(
    request: Request,
    payload: AutoPersonalizedVPTIRequest,
    background: BackgroundTasks,
) -> PersonalizedVPTIResponse:
    """좌표 + 애플워치 생체신호만으로 pVPTI 자동 산출(B2).

    orchestrator 가 Street View+SegFormer(공간)·KMA(기상)를 자동 조회해 vpti_core
    PET+PHI 로 pVPTI 를 낸다. 캐시 hit 시 빠름. biometrics 는 계산 후 폐기(미저장·미로깅).
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized. Backend may still be starting.",
        )

    bio = Biometrics(
        hr=payload.biometrics.hr,
        activity=payload.biometrics.activity,
        hr_rest=payload.biometrics.hr_rest,
        hr_max=payload.biometrics.hr_max,
    )
    profile = None
    if payload.profile is not None:
        profile = PhysiologyProfile(
            age=payload.profile.age,
            sex=payload.profile.sex,
            height_cm=payload.profile.height_cm,
            weight_kg=payload.profile.weight_kg,
            observed_hr_max=payload.profile.observed_hr_max,
            conditions=tuple(payload.profile.conditions or ()),
        )

    lat, lon = payload.location.lat, payload.location.lon
    heading = _resolve_heading(request, lat, lon, payload.heading, payload.session_id)

    try:
        result, telemetry = await orchestrator.compute_personalized(
            lat=lat,
            lon=lon,
            bio=bio,
            profile=profile,
            timestamp=payload.timestamp,
            # 익명 측정 기록 수집 동의 — 앱에서 전달. 미전달(구버전 앱)이면 수집하지 않음.
            archive_consent=bool(payload.archive_consent),
        )
    except StreetViewNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline error: {e}",
        ) from e

    # lookahead: 이미 prefetch로 캐시된 앞 지점만 캐시 전용 조회(콜드 계산 없음 → 응답 안 막음).
    # 아직 prefetch 안 된 지점은 생략(첫 방문지 첫 요청은 빈 배열).
    lookahead: list[LookaheadItem] = []
    if heading is not None:
        for dist in PREFETCH_DISTANCES_M:
            alat, alon = destination_point(lat, lon, heading, dist)
            try:
                ahead = await orchestrator.peek_personalized(
                    alat, alon, bio, profile, payload.timestamp
                )
            except Exception:  # noqa: BLE001
                ahead = None
            if ahead is not None:
                lookahead.append(LookaheadItem(
                    distance_m=dist,
                    pvpti=round(ahead.pvpti, 2),
                    risk_level=ahead.risk_level,
                ))

    # 응답 후 백그라운드로 진행 방향 앞 지점 prefetch (heading 있을 때만) — 다음 요청의 lookahead 채움
    if heading is not None:
        background.add_task(
            orchestrator.prefetch_ahead, lat, lon, heading, payload.speed_kmh
        )

    # 하늘상태 표시값 — 운량 [0,1] → KMA 구간(맑음 0~5.5/구름많음 ~8.5/흐림)
    # ⚠️ 이 블록은 0f28a76 에서 추가됐다가 0414b95(아카이브)에서 실수로 지워졌던 것 —
    #    2026-08-12 복구. 응답의 sky_desc 가 빠지면 앱 하늘 카드가 통째로 사라진다.
    sky_desc = None
    if telemetry.cloud_fraction is not None:
        cf = telemetry.cloud_fraction
        sky_desc = "맑음" if cf < 0.55 else ("구름많음" if cf < 0.85 else "흐림")

    return PersonalizedVPTIResponse(
        **result.as_dict(),
        weather_source=telemetry.weather_source,
        sky_desc=sky_desc,
        sky_source=telemetry.cloud_source,
        cloud_fraction=telemetry.cloud_fraction,
        lookahead=lookahead,
    )


@router.get(
    "/quota", summary="거리영상 월 사용량·잔여 (관리자용)",
)
async def imagery_quota(request: Request) -> JSONResponse:
    """구글 거리뷰 이번 달 사용량. 요금이 나가기 전에 눈으로 볼 수 있어야 한다.

    요금 구조(2026-08 확인): **월 10,000장 무료**, 초과 1,000장당 $7.
    파노라마 1지점 = 5장이므로 무료로 월 2,000개 신규 지점, 그 뒤 지점당 약 50원.
    좌표→파노라마 조회(메타데이터)는 무제한 무료라 여기 안 잡힌다.
    """
    s = get_settings()
    cache = getattr(request.app.state, "cache", None)
    ym = datetime.now(timezone.utc).strftime("%Y%m")
    used = 0
    if cache is not None:
        try:
            used = await cache.get_imagery_fetch_count(ym)
        except Exception as e:  # noqa: BLE001
            logger.warning("[quota] 조회 실패: {}", e)
    budget = s.streetview_monthly_image_budget
    remain = max(0, budget - used) if budget > 0 else None
    return JSONResponse({
        "month": ym,
        "images_used": used,
        "images_budget": budget,
        "images_remaining": remain,
        "points_used": used // 5,
        "points_remaining": (remain // 5) if remain is not None else None,
        "pct": round(used / budget * 100, 1) if budget > 0 else None,
        "note": "1지점=5장 · 구글 무료 월 10,000장 · 초과 1,000장당 $7",
    })


@router.get(
    "/shelters",
    summary="무더위쉼터 — 내 위치 주변 (전국 61,017곳, 행정안전부)",
)
async def shelters(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    radius: float = Query(3000.0, ge=100.0, le=20000.0, description="반경 m"),
    limit: int = Query(200, ge=1, le=1000),
) -> JSONResponse:
    """앱 안에 부산 1,688곳이 하드코딩돼 있던 것을 대체한다.

    스토어에 올리면 전국에서 내려받는데 부산 데이터만 들고 있으면
    다른 지역 사용자에게는 쉼터 기능이 통째로 먹통이 된다.
    """
    from app.services import shelters as sh

    items = sh.nearby(lat, lon, radius_m=radius, limit=limit)
    return JSONResponse({
        "count": len(items), "radius_m": radius,
        "total_nationwide": sh.count(),
        "shelters": items,
    })


@router.get(
    "/roads",
    summary="보행 도로망 — 타일 캐시 (쾌적 경로용)",
)
async def roads(
    request: Request,
    bbox: str = Query(..., description="minLat,minLon,maxLat,maxLon"),
    detail: str = Query("lite", pattern="^(lite|full)$",
                        description="lite=단지내 도로 제외(빠름) / full=골목 포함"),
) -> JSONResponse:
    """앱이 공개 Overpass 를 직접 부르던 것을 대신한다.

    같은 OSM 원본이라 **정확도는 그대로**고, 바뀌는 건 "누가 몇 번 받느냐"뿐이다.
    타일(0.02도 ≈ 2km)마다 한 번만 받아 영구 보관하므로, 그 동네 첫 요청만
    기다리고 그 뒤 모든 사용자는 즉시 받는다.
    """
    from app.services.roadnet import RoadNetError, RoadNetService

    try:
        parts = [float(v) for v in bbox.split(",")]
        if len(parts) != 4:
            raise ValueError
        min_lat, min_lon, max_lat, max_lon = parts
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bbox 형식이 잘못됐습니다. minLat,minLon,maxLat,maxLon",
        ) from None
    if not (-90 <= min_lat < max_lat <= 90 and -180 <= min_lon < max_lon <= 180):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="bbox 범위가 잘못됐습니다.")

    svc = getattr(request.app.state, "roadnet", None)
    if svc is None:
        svc = RoadNetService(getattr(request.app.state, "cache", None))
        request.app.state.roadnet = svc

    t0 = _time.perf_counter()
    try:
        out = await svc.bbox(min_lat, min_lon, max_lat, max_lon, detail=detail)
    except RoadNetError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=str(e)) from e
    out["meta"]["elapsed_ms"] = round((_time.perf_counter() - t0) * 1000)
    logger.info("roads {} detail={} tiles={} hits={} {}ms",
                bbox, detail, out["meta"]["tiles"], out["meta"]["cache_hits"],
                out["meta"]["elapsed_ms"])
    return JSONResponse(out)


@router.get(
    "/poi/near",
    summary="내 주변 장소 — 목적지 고르기용 (식당·카페·관광지)",
)
async def poi_near(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    radius: int = Query(600, ge=100, le=1500, description="반경 m"),
    kinds: str = Query("restaurant,cafe,attraction",
                       description="restaurant,cafe,bar,attraction,shrine,park,convenience,toilets,water"),
    lang: str = Query("ja", description="이름 우선 언어 (ja/en/ko)"),
) -> JSONResponse:
    """목적지를 고르기 위한 목록이다. **거리순**이며 쾌적도로 정렬하지 않는다 —
    사람은 갈 곳을 먼저 정하고, 쾌적함은 그 길을 어떻게 갈지의 문제다.
    고른 지점을 /route/shade 의 to_lat/to_lon 으로 넘기면 그늘 경로가 나온다.
    출처: © OpenStreetMap contributors (ODbL).
    """
    from app.services import poi as _poi
    ks = tuple(k.strip() for k in kinds.split(",") if k.strip() in _poi.KINDS)
    if not ks:
        ks = _poi.DEFAULT_KINDS
    try:
        items = await _poi.near(lat, lon, radius, ks, lang=lang)
    except Exception as e:  # noqa: BLE001
        logger.warning("poi/near 실패: {}", e)
        items = []
    return JSONResponse({"ok": True, "count": len(items), "radius": radius,
                         "items": items,
                         "attribution": "© OpenStreetMap contributors"})


@router.get(
    "/poi/stations",
    summary="가까운 역과 출구 — 일본 일상 이동의 기준점",
)
async def poi_stations(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    radius: int = Query(1200, ge=200, le=1500, description="반경 m"),
    lang: str = Query("ja", description="이름 우선 언어 (ja/en/ko)"),
) -> JSONResponse:
    """일본의 하루 이동은 역을 축으로 돈다(집→역, 역→직장·관광지). 그 기준점을 한 번에 준다.

    출구(`entrances`)를 함께 주는 이유: 큰 역은 출구가 열 개가 넘고, 어느 출구로 나오느냐로
    걷는 길이 통째로 달라진다. 출구마다 `/route/shade` 를 돌리면 **어느 출구가 시원한지**를
    답할 수 있다 — 지도 앱이 답해 주지 않는 질문이다.
    출구가 매핑돼 있지 않은 역은 `entrances` 가 빈 배열이다(없는 것을 지어내지 않는다).
    출처: © OpenStreetMap contributors (ODbL).
    """
    from app.services import poi as _poi
    try:
        items = await _poi.stations(lat, lon, radius, lang=lang)
    except Exception as e:  # noqa: BLE001
        logger.warning("poi/stations 실패: {}", e)
        items = []
    return JSONResponse({"ok": True, "count": len(items), "radius": radius,
                         "items": items,
                         "attribution": "© OpenStreetMap contributors"})


@router.get(
    "/poi/along",
    summary="경로 위의 장소 — 가는 길에 뭐가 있나",
)
async def poi_along(
    path: str = Query(..., description="경로 좌표 'lat,lon;lat,lon;…' (최대 300점)"),
    radius: int = Query(150, ge=50, le=400, description="경로에서 벗어나는 허용 거리 m"),
    kinds: str = Query("restaurant,cafe,attraction"),
    lang: str = Query("ja"),
) -> JSONResponse:
    """/route/shade 로 받은 coords 를 그대로 넘기면 그 길 위의 장소를 돌려준다.
    정렬은 **가는 순서**(출발지로부터 경로상 거리)이며, 각 항목에 경로에서 벗어나는
    거리(detour_m)와 경로상 위치(at_m/at_pct)를 함께 준다.
    출처: © OpenStreetMap contributors (ODbL).
    """
    from app.services import poi as _poi
    pts: list[tuple[float, float]] = []
    for part in path.split(";")[:300]:
        try:
            a, b = part.split(",")
            la, lo = float(a), float(b)
        except (ValueError, TypeError):
            continue
        if -90 <= la <= 90 and -180 <= lo <= 180:
            pts.append((la, lo))
    if len(pts) < 2:
        return JSONResponse({"ok": False, "reason": "경로 좌표가 부족합니다.", "items": []})
    ks = tuple(k.strip() for k in kinds.split(",") if k.strip() in _poi.KINDS) or _poi.DEFAULT_KINDS
    try:
        items = await _poi.along(pts, radius, ks, lang=lang)
    except Exception as e:  # noqa: BLE001
        logger.warning("poi/along 실패: {}", e)
        items = []
    return JSONResponse({"ok": True, "count": len(items), "radius": radius,
                         "items": items,
                         "attribution": "© OpenStreetMap contributors"})


@router.get(
    "/route/shade",
    summary="A→B 그늘 우선 경로(日陰ルート) — 최단 경로와 나란히",
)
async def route_shade(
    request: Request,
    from_lat: float = Query(..., ge=-90.0, le=90.0),
    from_lon: float = Query(..., ge=-180.0, le=180.0),
    to_lat: float = Query(..., ge=-90.0, le=90.0),
    to_lon: float = Query(..., ge=-180.0, le=180.0),
    air_c: float | None = Query(None, description="기온 °C. 생략하면 현재 날씨를 서버가 조회"),
    ghi: float | None = Query(None, ge=0.0, le=1400.0),
    wind_ms: float | None = Query(None, ge=0.0, le=40.0),
    rh: float | None = Query(None, ge=0.0, le=100.0),
    height_cm: float = Query(160.0, ge=20.0, le=200.0,
                             description="기준 높이(cm). 성인 160, 유모차·아이 60, 개는 체고"),
    vuln_offset_c: float = Query(0.0, ge=-5.0, le=10.0),
    mode: str = Query("walk", pattern="^(walk|bike)$",
                      description="walk=보행, bike=자전거(맞바람·대사량·계단제외 반영)"),
    speed_kmh: float | None = Query(None, ge=5.0, le=35.0,
                                    description="자전거 주행 속도. 생략하면 15 km/h"),
    via_lat: float | None = Query(None, ge=-90.0, le=90.0,
                                  description="경유지. 주면 출발→경유→도착을 한 번에 계산"),
    via_lon: float | None = Query(None, ge=-180.0, le=180.0),
    at: str | None = Query(None, description="출발 시각 ISO 8601 (예: 2026-08-01T12:00+09:00). "
                                             "생략하면 지금. 태양 위치·일사를 이 시각으로 계산한다"),
) -> JSONResponse:
    """출발→목적지 편도. **산책 코스와 같은 비용 함수**(그늘·노면온도·WBGT)를 쓰고 모양만 편도다.

    `mode=bike` (2026-09-16) — 일본은 자전거 분담률이 높다(역까지 자전거 + 전철이 일상).
    보행과 물리적으로 다른 점만 바꾼다.
      · **속도가 곧 바람**: 15 km/h = 4.2 m/s 맞바람. WBGT·PET·노면 대류가 같이 바뀐다.
      · **대사량**: 보행 2.0 MET → 자전거 4.5 MET. PET 입력값이라 고정하면 틀린다.
      · **계단 제외**: 남겨 두면 지도에 선은 그려지는데 실제로는 못 가는 거짓 경로가 된다.
      · **자전거도로는 비용에 넣지 않는다** (2026-09-16 정정). 레인이 얼마나 더 쾌적한지에
        대한 연구 근거가 없어 임의 배수를 넣었다가, 그게 물리(그늘)를 뒤집는 것을 확인하고
        걷어냈다. 레인·보도 비율은 결과에 **표시만** 한다.
    눈높이는 보행과 같게 둔다 — 자전거 탄 사람 눈높이가 보행자와 비슷하다(약 1.4 m).
    개(`height_cm`)가 특별했던 건 달궈진 노면 복사와 발 화상 때문이다.

    왜 필요한가 (2026-09-12): 일본의 실제 위험 구간은 레저 산책이 아니라 **역까지 걷는 통근·통학**이다.
    그늘 경로만 주면 "얼마나 이득인지"를 모르니 **최단 경로를 같이** 돌려준다 —
    "2분 더 걸으면 그늘 3배, 노면 8°C 낮음" 이 비교가 있어야 사람이 실제로 길을 바꾼다.
    height_cm 은 유모차·아이·개처럼 지면에 가까울수록 복사열을 더 받는 것을 반영한다.
    """
    from app.services import dog_course as dc
    from app.services.roadnet import RoadNetError, RoadNetService

    from app.services.dog_course import _haversine
    # 경유지가 있으면 **세 점을 모두 덮는** 범위여야 한다. 출발·도착만으로 잡으면
    # 옆으로 벗어난 가게가 도로망 밖으로 떨어져 경로가 끊긴다.
    _pts = [(from_lat, from_lon), (to_lat, to_lon)]
    has_via = via_lat is not None and via_lon is not None
    if has_via:
        _pts.append((via_lat, via_lon))
    _las = [p[0] for p in _pts]
    _los = [p[1] for p in _pts]
    span = (max(_las) - min(_las)) / 2 + 0.006
    lon_span = (max(_los) - min(_los)) / 2 + 0.008
    c_lat, c_lon = (max(_las) + min(_las)) / 2, (max(_los) + min(_los)) / 2
    _far = _haversine(from_lat, from_lon, to_lat, to_lon)
    if has_via:
        _far = (_haversine(from_lat, from_lon, via_lat, via_lon)
                + _haversine(via_lat, via_lon, to_lat, to_lon))
    if _far > 8000:
        raise HTTPException(status_code=400, detail="출발지와 목적지가 너무 멉니다(8km 이내).")

    svc = getattr(request.app.state, "roadnet", None)
    if svc is None:
        svc = RoadNetService(request.app.state.cache)
        request.app.state.roadnet = svc
    t0 = _time.perf_counter()
    try:
        roads = await svc.bbox(c_lat - span, c_lon - lon_span, c_lat + span, c_lon + lon_span,
                               detail="full")
    except RoadNetError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e

    # 출발 시각 (2026-09-24). 지금까지 태양 위치를 늘 서버의 「지금」으로 잡았다.
    # 그러면 밤에 한낮 조건(ghi=850)을 넣어 시험하면 해가 지평선 아래라 직달이 0 이 되고,
    # 「몇 시에 나가면 시원한가」(출발 시각 비교)도 만들 수 없다.
    from datetime import datetime as _dtw, timezone as _tzw
    try:
        _when = _dtw.fromisoformat(at) if at else _dtw.now(_tzw.utc)
        if _when.tzinfo is None:
            _when = _when.replace(tzinfo=_tzw.utc)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"at 형식 오류: {at}") from e

    # 날씨 미지정이면 서버가 조회(앱이 날씨를 따로 부르지 않게)
    if air_c is None or rh is None or wind_ms is None or ghi is None:
        from app.services.open_meteo import get_current_observation
        from vpti_core import estimate_solar as _es2, DEFAULT_CONFIG as _CFG
        obs = await get_current_observation(c_lat, c_lon)
        sol = _es2(c_lat, c_lon, _when, config=_CFG.solar)
        air_c = obs.temperature_c if air_c is None else air_c
        rh = obs.humidity_pct if rh is None else rh
        wind_ms = obs.wind_speed_ms if wind_ms is None else wind_ms
        ghi = float(getattr(sol, "ghi", 0.0) or 0.0) if ghi is None else ghi

    cond = dc.Conditions(air_c=air_c, ghi=ghi or 0.0, wind_ms=wind_ms, rh=rh,
                         withers_cm=height_cm, vuln_offset_c=vuln_offset_c,
                         mode=mode, speed_kmh=speed_kmh or 0.0)
    _sky_cells, _sun = {}, None
    try:
        from datetime import datetime as _dt, timezone as _tz
        from vpti_core import estimate_solar as _es
        from app.services import skyline as _sky
        _s = _es(c_lat, c_lon, _when)
        cond.sol = _s        # 정식 MRT 용 (2026-09-24) — 직달·산란 분해와 태양 위치
        if _s.solar_elevation_deg > 0:
            _sun = (_s.solar_azimuth_deg, _s.solar_elevation_deg)
            _sky_cells = await _sky.get_cells(dc.edge_midpoints(roads.get("elements", [])))
    except Exception as _e:  # noqa: BLE001
        logger.warning("route/shade 스카이라인 조회 생략: {}", _e)

    graph = dc.build_graph(roads.get("elements", []), cond, skyline=_sky_cells, sun=_sun)
    a = dc.nearest(graph, from_lat, from_lon)
    b = dc.nearest(graph, to_lat, to_lon)
    if a is None or b is None or a[0] == b[0]:
        return JSONResponse({"ok": False, "reason": "주변에서 걸을 수 있는 길을 찾지 못했어요."})
    _via = dc.nearest(graph, via_lat, via_lon) if has_via else None
    if has_via and _via is None:
        return JSONResponse({"ok": False, "reason": "경유지 주변에서 길을 찾지 못했어요."})
    res = dc.find_route(graph, a[0], b[0], via=(_via[0] if _via else None))
    if not res.get("comfort"):
        return JSONResponse({"ok": False, "reason": "두 지점을 잇는 보행 경로를 찾지 못했어요."})
    return JSONResponse({
        "ok": True, **res,
        "weather": {"ta": round(air_c, 1), "rh": round(rh, 0),
                    "wind_ms": round(wind_ms, 1), "ghi": round(ghi or 0.0)},
        "meta": {"edges": graph.edge_count, "snap_from_m": round(a[1]), "snap_to_m": round(b[1]),
                 **({"snap_via_m": round(_via[1])} if _via else {}),
                 "skyline_cells": len(_sky_cells),
                 "skyline_shaded_edges": graph.skyline_shaded_edges,
                 "elapsed_ms": round((_time.perf_counter() - t0) * 1000)},
    })


@router.get(
    "/dog/course",
    summary="개 기준 산책 코스 추천 — 출발점으로 되돌아오는 순환 코스 3개",
)
async def dog_course(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    minutes: int = Query(30, ge=10, le=120, description="목표 산책 시간(분)"),
    air_c: float | None = Query(None, description="기온 °C. 생략하면 현재 날씨를 서버가 조회"),
    ghi: float | None = Query(None, ge=0.0, le=1400.0, description="유효 일사 W/m²"),
    wind_ms: float | None = Query(None, ge=0.0, le=40.0),
    rh: float | None = Query(None, ge=0.0, le=100.0),
    rain: bool = Query(False),
    withers_cm: float = Query(45.0, ge=10.0, le=100.0, description="개 체고(cm)"),
    vuln_offset_c: float = Query(0.0, ge=-5.0, le=10.0, description="개체 취약도 오프셋 °C"),
) -> JSONResponse:
    """도로망 위에서 **개 기준으로 가장 시원한 순환 코스**를 찾아준다.

    왜 서버가 하나 (2026-08-25):
      코스 계산은 도로망을 받아 다익스트라를 도는 무거운 일이다. 폰에서 하면 배터리를
      먹고 느리며, iOS(Swift)와 안드로이드(Kotlin)에 같은 로직을 두 벌 유지해야 한다.
      서버가 계산하면 두 앱이 **같은 답**을 보고, 알고리즘 개선이 **앱 심사 없이** 반영된다.

    ⚠️ 점수 일관성: 구간 비용 = WBGT(개 높이) + 취약도오프셋 + max(0, 노면온도 − 44).
       시간대 화면(WalkWindow)과 같은 식이라 두 화면이 다른 말을 하지 않는다.

    ⚠️ 근거의 층위: 노면 재질·그늘은 OSM 태그에서 **읽고**, 태그가 없으면 도로 유형으로
       **추정**한다. 추정 비율은 `surface_known_ratio` 로 함께 돌려주니 화면에 밝힐 것.
    """
    from app.services import dog_course as dc
    from app.services.roadnet import RoadNetError, RoadNetService

    # 목표 거리 = 시간 × 산책 속도. 도로망은 그 절반 반경이면 충분하다.
    target_m = minutes / 60.0 * dc.WALK_SPEED_KMH * 1000.0
    span_deg = max(target_m * 0.6, 500.0) / 111_000.0
    lon_span = span_deg / max(0.2, _math.cos(_math.radians(lat)))

    svc = getattr(request.app.state, "roadnet", None)
    if svc is None:
        svc = RoadNetService(getattr(request.app.state, "cache", None))
        request.app.state.roadnet = svc

    t0 = _time.perf_counter()
    try:
        roads = await svc.bbox(lat - span_deg, lon - lon_span,
                               lat + span_deg, lon + lon_span, detail="full")
    except RoadNetError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e

    # 날씨 미지정이면 서버가 조회한다 (2026-09-13) — 앱이 날씨를 따로 부르지 않게.
    # route/shade 와 같은 규약이라 두 화면이 서로 다른 기상으로 답하지 않는다.
    if air_c is None or rh is None or wind_ms is None or ghi is None:
        from datetime import datetime as _dt3, timezone as _tz3
        from app.services.open_meteo import get_current_observation
        from vpti_core import estimate_solar as _es3, DEFAULT_CONFIG as _CFG3
        obs = await get_current_observation(lat, lon)
        sol3 = _es3(lat, lon, _dt3.now(_tz3.utc), config=_CFG3.solar)
        air_c = obs.temperature_c if air_c is None else air_c
        rh = obs.humidity_pct if rh is None else rh
        wind_ms = obs.wind_speed_ms if wind_ms is None else wind_ms
        ghi = float(getattr(sol3, "ghi", 0.0) or 0.0) if ghi is None else ghi

    cond = dc.Conditions(air_c=air_c, ghi=ghi or 0.0, wind_ms=wind_ms, rh=rh,
                         rain=rain, withers_cm=withers_cm, vuln_offset_c=vuln_offset_c)
    # 스카이라인 격자 일괄 조회 → 건물 그늘 반영 (2026-09-11). 격자 없는 곳은 기존 OSM 태그 방식.
    _sky_cells, _sun = {}, None
    try:
        from datetime import datetime as _dt, timezone as _tz
        from vpti_core import estimate_solar as _es
        from app.services import skyline as _sky
        _s = _es(lat, lon, _dt.now(_tz.utc))
        cond.sol = _s        # 정식 MRT 용 (2026-09-24)
        if _s.solar_elevation_deg > 0:
            _sun = (_s.solar_azimuth_deg, _s.solar_elevation_deg)
            _sky_cells = await _sky.get_cells(dc.edge_midpoints(roads.get("elements", [])))
    except Exception as _e:  # noqa: BLE001
        logger.warning("dog/course 스카이라인 조회 생략: {}", _e)
    graph = dc.build_graph(roads.get("elements", []), cond, skyline=_sky_cells, sun=_sun)
    near = dc.nearest(graph, lat, lon)
    if near is None or graph.edge_count <= 10:
        return JSONResponse({
            "courses": [], "count": 0,
            "reason": "주변에서 걸을 수 있는 길을 찾지 못했어요.",
            "meta": {"edges": graph.edge_count,
                     "elapsed_ms": round((_time.perf_counter() - t0) * 1000)},
        })

    start, snap_m = near
    courses = dc.find_courses(graph, start, target_m)
    elapsed = round((_time.perf_counter() - t0) * 1000)
    logger.info("dog/course {},{} {}분 edges={} courses={} {}ms",
                lat, lon, minutes, graph.edge_count, len(courses), elapsed)
    return JSONResponse({
        "courses": courses,
        "count": len(courses),
        "target_meters": round(target_m),
        "meta": {
            "edges": graph.edge_count,
            "nodes": len(graph.coords),
            "snap_m": round(snap_m),
            "elapsed_ms": elapsed,
            "skyline_cells": len(_sky_cells),
            "skyline_shaded_edges": graph.skyline_shaded_edges,
        },
    })


@router.get(
    "/places/search",
    summary="장소 검색 — 상호·건물명·주소로 후보 목록 (카카오 로컬)",
)
async def places_search(
    query: str = Query(..., min_length=1, description="상호·건물명·주소 (예: 서면 스타벅스)"),
    lat: float | None = Query(None, ge=-90.0, le=90.0, description="현재 위치 위도(가까운 순 정렬)"),
    lon: float | None = Query(None, ge=-180.0, le=180.0, description="현재 위치 경도"),
    size: int = Query(10, ge=1, le=15, description="후보 개수"),
) -> JSONResponse:
    """목적지 후보를 **여러 개** 돌려준다 — 앱은 이걸 리스트로 그려 고르게 한다.

    기존 /geocode 는 답을 하나만 주고 못 찾으면 404였다. 사람은 주소를 외우지 않으므로
    "한 방에 정확히 맞히기"가 아니라 "후보를 보여주고 고르게 하기"가 맞는 구조다.

    lat/lon 을 함께 주면 가까운 곳이 위로 온다. 결과가 없어도 404가 아니라 빈 목록이다
    (사용자가 아직 타이핑 중일 수 있다 — 자동완성에서 404는 오류로 보인다).
    """
    from app.services.place_search import search_places

    s = get_settings()
    places = await search_places(
        s.kakao_rest_api_key, query, lat=lat, lon=lon, size=size
    )

    # 카카오 키가 없거나 결과가 비면 기존 경로(OSM 장소명)로 한 번 더 시도 — 하위호환
    if not places:
        from app.services.ncp_directions import nominatim_geocode

        fallback = await nominatim_geocode(query)
        if fallback is not None:
            flat, flon, flabel = fallback
            places = [{
                "name": query, "address": flabel, "category": "",
                "lat": flat, "lon": flon, "distance_m": None, "source": "osm",
            }]

    return JSONResponse({"query": query, "count": len(places), "places": places})


@router.get(
    "/reverse-geocode",
    summary="좌표 → 주소 이름표 (지도에서 찍은 지점용)",
)
async def reverse_geocode_at(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
) -> JSONResponse:
    """지도에서 찍은 지점의 표시용 이름을 만든다.

    ⚠️ 이름을 못 찾아도 **200과 함께 기본 이름을 돌려준다.** 경로 계산은 좌표만으로
    가능하므로, 이름 조회 실패가 목적지 선택을 취소시키면 안 된다 —
    "지도에서 선택한 지점을 못 찾았습니다"가 뜨던 원인이 바로 그 구조였다.
    """
    from app.services.place_search import reverse_geocode

    s = get_settings()
    label = await reverse_geocode(s.kakao_rest_api_key, lat, lon)
    return JSONResponse({
        "lat": lat, "lon": lon,
        "address": label or "지도에서 선택한 지점",
        "resolved": label is not None,
    })


@router.get("/geocode", summary="주소/장소 → 좌표 (카카오 → NCP 주소 → OSM 순)")
async def geocode(
    request: Request,
    query: str = Query(..., min_length=1, description="검색할 주소 또는 장소명"),
    lat: float | None = Query(None, ge=-90.0, le=90.0, description="현재 위치(가까운 순)"),
    lon: float | None = Query(None, ge=-180.0, le=180.0),
) -> JSONResponse:
    """목적지 문자열 → 좌표 1건. (구버전 앱 호환용 — 신규 화면은 /places/search 사용)

    1순위 카카오 로컬(상호·건물명), 2순위 NCP Geocoding(주소 정밀), 3순위 OSM.
    """
    from app.services.ncp_directions import nominatim_geocode
    from app.services.place_search import search_places

    s = get_settings()

    # 1) 카카오 — 상호·건물명이 여기서 잡힌다
    places = await search_places(s.kakao_rest_api_key, query, lat=lat, lon=lon, size=1)
    if places:
        p = places[0]
        return JSONResponse({
            "lat": p["lat"], "lon": p["lon"],
            "address": p["address"] or p["name"],
            "name": p["name"],
            "source": p["source"],
        })

    # 2) NCP 주소 검색
    directions = getattr(request.app.state, "directions", None)
    result = None
    source = None
    if directions is not None:
        try:
            result = await directions.geocode(query)
            if result is not None:
                source = "ncp"
        except Exception as e:  # noqa: BLE001
            logger.warning("NCP geocode 실패(무시): {}", e)

    # 3) 장소명 폴백 (OSM Nominatim)
    if result is None:
        result = await nominatim_geocode(query)
        if result is not None:
            source = "osm"

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="위치를 찾을 수 없습니다. 장소명이나 주소를 조금 더 구체적으로 입력해 보세요.",
        )
    glat, glon, label = result
    return JSONResponse({"lat": glat, "lon": glon, "address": label,
                         "name": label, "source": source})


@router.get(
    "/route",
    summary="출발→도착 경로를 지점별 VPTI로 산출 (NCP 길찾기)",
)
async def route_vpti(
    request: Request,
    olat: float = Query(..., ge=-90.0, le=90.0, description="출발 위도"),
    olon: float = Query(..., ge=-180.0, le=180.0, description="출발 경도"),
    dlat: float = Query(..., ge=-90.0, le=90.0, description="도착 위도"),
    dlon: float = Query(..., ge=-180.0, le=180.0, description="도착 경도"),
    max_points: int = Query(10, ge=2, le=20, description="샘플 지점 수(성능 상한)"),
) -> JSONResponse:
    """NCP 길찾기로 도로 경로를 받아, 균등 샘플 지점마다 VPTI를 산출한다.

    지점마다 Street View + 세그멘테이션이 필요해 첫 계산은 느릴 수 있다(캐시 후 빠름).
    강수는 VPTI와 분리된 컨텍스트 레이어로 함께 반환한다.
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    directions = getattr(request.app.state, "directions", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized.",
        )
    if directions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="경로 탐색 비활성 — NCP Maps 키(.env)를 설정하세요.",
        )

    from app.services.ncp_directions import NCPDirectionsError, sample_path

    try:
        path = await directions.get_path(olat, olon, dlat, dlon)
    except NCPDirectionsError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"길찾기 실패: {e}") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"길찾기 오류: {e}") from e

    samples = sample_path(path, max_points=max_points)

    # 지점별 계산. 거리뷰가 없는 지점(골목·아파트 단지 안 등)은 흔하다 —
    # 예전에는 그런 지점을 건너뛰다가 전부 실패하면 502로 요청 자체를 버렸다.
    # 그 결과 "경로를 찾을 수 없다"가 뜨고 지도에 선도 안 그려졌다.
    # 이제는 **경로는 항상 돌려주고**, 계산이 안 된 지점만 비워둔 뒤 이웃 값으로 메운다.
    points: list[dict] = []
    n_missing = 0
    for (lat, lon) in samples:
        try:
            result, _ = await orchestrator.compute(lat=lat, lon=lon)
        except StreetViewNotFound:
            n_missing += 1
            points.append({"lat": round(lat, 6), "lon": round(lon, 6),
                           "vpti": None, "estimated": False})
            continue
        except Exception as e:  # noqa: BLE001
            logger.warning("경로 지점 계산 실패({},{}): {}", lat, lon, e)
            n_missing += 1
            points.append({"lat": round(lat, 6), "lon": round(lon, 6),
                           "vpti": None, "estimated": False})
            continue
        d = result.as_dict()
        points.append({
            "lat": round(lat, 6), "lon": round(lon, 6),
            "vpti": d["vpti"], "risk_level": d["risk_level"],
            "contributions": d["contributions"], "action_guide": d["action_guide"],
            "estimated": False,
        })

    # 빈 지점을 가장 가까운 계산된 지점 값으로 채운다(estimated=True 로 표시).
    # 체감기후는 수십 m 스케일에서 연속적이므로 이웃 대입이 무근거한 창작은 아니다.
    # 다만 앱은 이 표시를 받아 "추정" 으로 그려야 한다 — 실측과 같게 보이면 안 된다.
    computed_idx = [i for i, p in enumerate(points) if p.get("vpti") is not None]
    if computed_idx:
        for i, p in enumerate(points):
            if p.get("vpti") is not None:
                continue
            j = min(computed_idx, key=lambda k: abs(k - i))
            src = points[j]
            p.update({
                "vpti": src["vpti"], "risk_level": src["risk_level"],
                "contributions": src["contributions"],
                "action_guide": src["action_guide"],
                "estimated": True,
            })
    else:
        # 경로 전체에 거리뷰가 하나도 없는 경우. 그래도 경로선과 기상은 돌려준다 —
        # 앱이 "경로를 못 찾았다"고 말하는 것보다 훨씬 낫다.
        logger.warning("경로 전 구간 거리뷰 부재 ({} 지점)", len(points))

    # 기상(계절/기온/습도) + 강수 컨텍스트
    weather_meta = {}
    precipitation = None
    try:
        w, _, _ = await orchestrator._get_weather(olat, olon)
        weather_meta = {
            "temperature_c": w.temperature_c, "humidity_pct": w.humidity_pct,
            "wind_speed_ms": w.wind_speed_ms, "season": w.season,
        }
        precipitation = await orchestrator.get_precipitation_outlook(olat, olon)
    except Exception as e:  # noqa: BLE001
        logger.warning("경로 기상/강수 부착 실패: {}", e)

    vs = [p["vpti"] for p in points if p.get("vpti") is not None]
    summary = (
        {
            "vpti_min": round(min(vs), 2), "vpti_max": round(max(vs), 2),
            "vpti_avg": round(sum(vs) / len(vs), 2),
        }
        if vs
        else {"vpti_min": None, "vpti_max": None, "vpti_avg": None}
    )
    profile = {
        "meta": {
            "origin": {"lat": olat, "lon": olon, "name": "현재 위치"},
            "dest": {"lat": dlat, "lon": dlon, "name": "목적지"},
            "n_points": len(points), "sample": False,
            # 몇 개 지점이 실제로 계산됐는지 — 앱이 "일부 구간은 추정" 을 표시할 근거
            "n_computed": len(vs),
            "n_estimated": sum(1 for p in points if p.get("estimated")),
            "coverage": "none" if not vs else ("full" if n_missing == 0 else "partial"),
            "note": "실시간 경로 — NCP 길찾기 + 지점별 VPTI",
            "weather": weather_meta,
            "precipitation": precipitation,
        },
        "summary": summary,
        "points": points,
    }
    return JSONResponse(profile)


@router.get(
    "/brief/daily",
    summary="아침 브리핑 — 오늘 예보 요약 (우산·더위·옷차림 판단 재료, 2026-08-09)",
)
async def daily_brief(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
) -> dict:
    """오늘(KST) 남은 시간대 단기예보를 요약해 반환.

    앱의 아침 브리핑 알림용. 좌표→예보 요약만 — 개인정보 없음·미저장.
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized.",
        )

    try:
        forecasts = await orchestrator.kma.get_short_term_forecast(lat, lon)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"KMA forecast failed: {e}")

    now = datetime.now(KST)
    today = [
        f for f in forecasts
        if f.forecast_for.date() == now.date()
        and f.forecast_for >= now - timedelta(hours=1)
    ]
    if not today:
        raise HTTPException(status_code=404, detail="오늘 예보 없음")

    temps = [f.temperature_c for f in today if f.temperature_c is not None]
    hums = [f.humidity_pct for f in today if f.humidity_pct is not None]
    rain_slots = [
        f for f in today
        if (f.precipitation_type not in (None, "없음"))
        or ((f.precipitation_mm or 0.0) > 0.0)
    ]
    sky_rank = {"맑음": 1, "구름많음": 3, "흐림": 4}
    worst = max((sky_rank.get(f.sky_condition or "", 0) for f in today), default=0)
    first_rain = min((f.forecast_for for f in rain_slots), default=None)

    # "하루종일 비"로 뭉개지 않도록 실제 비 시간대만 뽑는다 (2026-09-01).
    # 단기예보는 3시간 간격이므로 한 칸은 3시간을 대표한다.
    rain_window = None
    if rain_slots:
        ordered = sorted(rain_slots, key=lambda f: f.forecast_for)
        blocks, start, prev = [], ordered[0], ordered[0]
        for f in ordered[1:]:
            if f.forecast_for - prev.forecast_for <= timedelta(hours=3):
                prev = f
                continue
            blocks.append((start, prev))
            start = prev = f
        blocks.append((start, prev))
        rain_window = ", ".join(
            f"{a.forecast_for:%H}~{(b.forecast_for + timedelta(hours=3)):%H}시"
            for a, b in blocks
        )

    # 시간대별 브리핑 (2026-09-01) — 하루 한 줄로 뭉치면 실제 행동에 못 쓴다.
    # "낮 최고 31도, 비 소식 있음"만으로는 오후에 우산을 챙길지 알 수 없다.
    # 오전/오후/저녁으로 쪼개서 각 구간의 기온대·비·하늘을 따로 준다.
    # 이미 지나간 구간은 넣지 않는다(예보 목록 자체가 now-1h 이후로 걸러져 있다).
    BANDS = (("morning", "오전", 6, 12), ("afternoon", "오후", 12, 18),
             ("evening", "저녁", 18, 24))
    bands = []
    for key, label, h0, h1 in BANDS:
        slots = [f for f in today if h0 <= f.forecast_for.hour < h1]
        if not slots:
            continue
        b_temps = [f.temperature_c for f in slots if f.temperature_c is not None]
        b_rain = [
            f for f in slots
            if (f.precipitation_type not in (None, "없음"))
            or ((f.precipitation_mm or 0.0) > 0.0)
        ]
        b_pops = [
            f.precipitation_prob_pct for f in slots
            if getattr(f, "precipitation_prob_pct", None) is not None
        ]
        b_worst = max((sky_rank.get(f.sky_condition or "", 0) for f in slots), default=0)
        # 실제로 남아 있는 시각 범위를 준다. 아침 9시에 열면 오전은 6시가 아니라
        # 9시부터다 — 이미 지나간 시간을 범위에 넣으면 브리핑이 거짓말이 된다.
        bands.append({
            "key": key,
            "label": label,
            "from_hour": min(f.forecast_for.hour for f in slots),
            "to_hour": h1,          # 구간의 끝(24 = 자정)
            "t_max": max(b_temps) if b_temps else None,
            "t_min": min(b_temps) if b_temps else None,
            "rain": bool(b_rain),
            # 이 구간에서 비가 시작되는 시각 — "오후 3시부터" 처럼 쓴다
            "rain_from_hour": (
                min(f.forecast_for.hour for f in b_rain) if b_rain else None
            ),
            # 비가 그치는 시각 — 비 이후 첫 '안 오는' 칸의 시각. 끝까지 오면 구간 끝.
            # 예보 간격(1시간/3시간)에 의존하지 않으려고 다음 칸의 시각을 그대로 쓴다.
            "rain_to_hour": (
                next(
                    (
                        h for h in sorted(f.forecast_for.hour for f in slots)
                        if h > max(g.forecast_for.hour for g in b_rain)
                        and h not in {g.forecast_for.hour for g in b_rain}
                    ),
                    h1,
                )
                if b_rain else None
            ),
            "rain_type": b_rain[0].precipitation_type if b_rain else None,
            "pop_max": max(b_pops) if b_pops else None,
            "sky": {1: "맑음", 3: "구름많음", 4: "흐림"}.get(b_worst),
        })

    return {
        "date": now.strftime("%Y-%m-%d"),
        "t_max": max(temps) if temps else None,
        "t_min": min(temps) if temps else None,
        "humidity_max": max(hums) if hums else None,
        "rain_expected": bool(rain_slots),
        "first_rain_hour": first_rain.hour if first_rain is not None else None,
        "rain_type": rain_slots[0].precipitation_type if rain_slots else None,
        "sky_worst": {1: "맑음", 3: "구름많음", 4: "흐림"}.get(worst),
        "slots": len(today),
        # --- 신규(구버전 앱은 무시) ---
        "rain_window": rain_window,          # 예: "14~18시" — 없으면 None
        "rain_slot_count": len(rain_slots),
        "all_day_rain": bool(rain_slots) and len(rain_slots) >= max(1, len(today) - 1),
        # 오전/오후/저녁 — 구버전 앱은 이 키를 무시한다
        "bands": bands,
    }


@router.get(
    "/rain/at",
    summary="내 위치 비 정보 — 격자 보간 + 관측 사실 + 접근 판정 (2026-09-01)",
)
async def rain_at(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
) -> dict:
    """좌표 단위 강수 정보.

    * 지금 비가 오는가 — 초단기실황(인접 격자 보간) + 주변 관측소 실측
    * 0~6시간 — 초단기예보를 인접 격자로 보간해 시간대별로
    * 접근 중인 비 — 바람 불어오는 쪽 관측소에 비가 있으면 도착시간 **범위**

    도착시간은 지상풍으로 강수대 이동을 근사한 **추정**이다. 단일 시각으로 단정하지
    않고 범위와 신뢰도를 함께 준다. 호우·태풍은 기상청 특보를 그대로 따라야 한다.
    개인정보 없음·미저장.
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized.",
        )
    try:
        return await orchestrator.get_precipitation_outlook(lat, lon)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"rain lookup failed: {e}")


@router.get("/field", include_in_schema=False)
async def field_tool_page():
    """현장 대조 도구 페이지 (대표 전용) — 서버가 직접 서빙 (2026-08-16).

    http://<서버>/api/v1/field 를 아이폰 사파리로 열고 "홈 화면에 추가"하면
    홈 화면 아이콘으로 수시 사용 가능. 같은 서버 상대경로 호출이라
    HTTPS 전환 전에도 동작한다. 페이지 자체는 공개돼도 무해 — 저장(POST)은
    FIELD_KEY 없이는 404 라 아무것도 못 한다.
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    p = Path(__file__).resolve().parents[1] / "web" / "field_admin.html"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    # no-store (2026-09-15): 홈 화면에 추가한 아이폰 웹앱이 옛 화면을 계속 띄웠다.
    # 현장 도구라 배포 즉시 반영돼야 한다. 페이지가 작아 매번 받아도 부담이 없다.
    return FileResponse(p, media_type="text/html",
                        headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/field/targets", include_in_schema=False)
async def field_targets():
    """실측 '갈 지점' 좌표 목록 (2026-09-15).

    `/api/v1/field` 페이지가 FileResponse 로 서빙돼 상대경로 정적파일을 못 읽는다 →
    JSON 을 라우트로 따로 연다. 파일은 `app/web/field_targets.json` 이고 이미지에 포함된다.
    좌표만 갱신하고 배포하면 현장 앱 목록이 바뀐다 — 앱 코드는 안 건드린다.
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    p = Path(__file__).resolve().parents[1] / "web" / "field_targets.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(p, media_type="application/json",
                        headers={"Cache-Control": "no-store"})


@router.get("/field/stats", include_in_schema=False)
async def field_stats(
    request: Request,
    x_field_key: str | None = Header(None),
) -> dict:
    """'뇌 상태' — 쌓인 학습 데이터 현황 (대표 전용, 2026-08-16).

    눈에 안 보이던 데이터 플라이휠을 숫자로 보여준다: 테이블별 적재 건수,
    최근 48h Tsurf 실측↔추정 잔차, 최근 현장실측 5건.
    """
    s = get_settings()
    if not s.field_key or x_field_key != s.field_key:
        raise HTTPException(status_code=404, detail="Not Found")
    archive = getattr(request.app.state, "archive", None)
    stats = await archive.brain_stats() if archive is not None else None
    if stats is None:
        return {"ready": False}
    return {"ready": True, **stats}


@router.post(
    "/field/check",
    summary="현장 실측 ↔ 엔진 대조 (대표 전용, 2026-08-16)",
)
async def field_check(
    request: Request,
    body: dict = Body(...),
    x_field_key: str | None = Header(None),
) -> dict:
    """실측값을 받아 **같은 순간 같은 좌표의 엔진 전체 산출**과 맞대고, 짝을 DB에 남긴다.

    현장실측 도구(climax_field)의 수동 대조를 자동화한 것:
      · 도구가 실측(기온·습도·풍속·흑구·표면온도·계산 PET)을 POST
      · 서버가 실물 파이프라인(orchestrator)을 그 좌표에서 돌려 산출 전체를 응답
      · 실측·산출 짝을 field_check 테이블에 적재 → PWI 풍속 보정, MRT/Tsurf 검증,
        계수 교정의 원천 데이터가 자동 축적된다

    접근 통제: .env 의 FIELD_KEY 와 X-Field-Key 헤더가 일치해야 한다.
    FIELD_KEY 미설정이면 404 — 일반 사용자에겐 엔드포인트 존재 자체가 안 보인다.
    """
    s = get_settings()
    if not s.field_key or x_field_key != s.field_key:
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        lat = float(body["lat"])
        lon = float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="lat/lon 필요")
    meas: dict = body.get("meas") or {}
    note: str | None = body.get("note")

    # 실측 시각 (2026-09-16). 폰에 접근키가 없어 저장이 막힌 사이 잰 값을 캡처로만
    # 남긴 일이 있었다. 그걸 나중에 올리면 **지금 태양**으로 엔진 값이 만들어져
    # 실측-엔진 짝의 시각이 어긋난다 — 그늘이었던 지점이 양지로 계산된다.
    # `when` (ISO 8601, 예: "2026-09-16T13:27:00+09:00") 을 주면 그 시각의 태양으로 푼다.
    # ⚠️ 한계: 날씨(기온·습도·풍속)는 여전히 **지금** 관측이다. 과거 기상 재조회는
    #    아직 없다. 그래서 같은 날 몇 시간 안의 복원에만 쓸 것. 실측 기상은 meas 로
    #    따로 들어오므로 잔차 계산에는 영향이 없다.
    _when = None
    if body.get("when"):
        try:
            _when = datetime.fromisoformat(str(body["when"]))
            if _when.tzinfo is None:
                _when = _when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="when 은 ISO 8601 이어야 합니다")

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="엔진 미기동")

    # 과거 실측 시각의 기상 (2026-09-17). `when` 만 주면 태양만 복원되고 날씨는 "지금"
    # 것이 쓰였다 — 2026-09-16 네 지점이 실측 29.8~30.2 °C 인데 엔진은 넷 다 26.7 °C 였고,
    # 그 3.5도가 통째로 MRT 잔차로 넘어갔다. 이제 기상도 그 시각 것으로 가져온다.
    # 실패하면 현재 기상으로 조용히 넘어가되, 무엇을 썼는지 est.weather_at 에 남긴다.
    _wx = None
    _wx_note = None
    if _when is not None and abs((datetime.now(timezone.utc) - _when).total_seconds()) > 1800.0:
        try:
            from app.services.open_meteo import get_observation_at
            _wx = await get_observation_at(lat, lon, _when)
            _wx_note = _wx.observed_at.isoformat()
        except Exception as e:  # noqa: BLE001
            _wx_note = f"실패({type(e).__name__}) — 현재 기상 사용"

    result, telemetry = await orchestrator.compute_personalized(
        lat, lon, bio=Biometrics(), archive_consent=False, timestamp=_when,
        weather_override=_wx,
    )
    base = result.comfort  # ComfortResult (PET/UTCI 입력 echo 포함)
    est = {
        "pvpti": round(result.pvpti, 2),
        "risk": result.risk_level,
        "index": base.index,
        "mrt": round(base.tr, 2),          # 엔진 Tmrt — **사람 기준**
        # 흑구가 읽었을 Tmrt (2026-09-16). 현장 흑구계(Extech HT200, Ø50mm)와 맞댈 값은 이쪽이다.
        # 사람 기준과 비교하면 정의차(구는 fp 0.25 고정·흡수 0.95, 사람은 fp 0.08~0.3·0.7)가
        # 통째로 잔차로 잡힌다 — 9/5 문서의 "MRT 잔여 +9.6°는 정의차"가 그것이다.
        "mrt_globe": (round(result.tmrt_globe, 2)
                      if getattr(result, "tmrt_globe", None) else None),
        "ta": round(base.tdb, 2),          # 엔진이 쓴 기온(기상청)
        "rh": round(base.rh, 1),
        "u_p": round(base.v_input, 2),     # 보행자 풍속 (클램프 전)
        "weather_source": telemetry.weather_source,
        # 어느 시각의 태양으로 푼 값인지 남긴다. 나중에 짝을 가릴 때 이게 없으면
        # 복원분인지 현장분인지 구분할 수 없다.
        "solar_at": _when.isoformat() if _when else None,
        # 어느 시각의 **날씨**로 푼 값인지. None 이면 현재 기상이다.
        "weather_at": _wx_note,
    }

    # 잔차 — 실측이 있는 항목만
    resid = {}
    for k_meas, k_est in (("ta", "ta"), ("rh", "rh"), ("wind_ms", "u_p"),
                          ("pet", "pvpti"), ("mrt", "mrt")):
        if meas.get(k_meas) is not None and est.get(k_est) is not None:
            resid[k_est] = round(est[k_est] - float(meas[k_meas]), 2)

    # 흑구 잔차 (2026-09-16). 현장에서 재는 것은 흑구온도(globe_c)인데 여기서 아무 잔차도
    # 만들지 않아, 실측을 저장해도 **MRT 대조가 전혀 쌓이지 않고 있었다.**
    # 실측을 변환하지 않는다 — 엔진의 흑구 예측(mrt_globe)과 실측 흑구온도에서 같은 식으로
    # 구한 Tmrt 를 맞댄다. ISO 7726 강제대류, Extech HT200 은 Ø50mm·ε0.95.
    _tg = meas.get("globe_c")
    if _tg is not None and est.get("mrt_globe") is not None:
        try:
            _ta = float(meas.get("ta", est["ta"]))
            _v = max(float(meas.get("wind_ms", est["u_p"])), 0.05)
            _tg = float(_tg)
            _D, _eps = float(body.get("globe_d_m") or 0.05), 0.95
            _obs = (((_tg + 273.15) ** 4
                     + 1.1e8 * _v ** 0.6 / (_eps * _D ** 0.4) * (_tg - _ta)) ** 0.25) - 273.15
            resid["mrt_globe"] = round(est["mrt_globe"] - _obs, 2)
            est["mrt_globe_obs"] = round(_obs, 2)
            est["globe_d_m"] = _D

            # 흑구온도 공간 잔차 (2026-09-17). 위 mrt_globe 잔차는 **서로 다른 바람**으로
            # 만든 두 값을 뺀 것이다 — 엔진은 u_p(≈2.3 m/s)로 흑구를 예측하고, 환산은
            # 실측 풍속(0.7)으로 되돌렸다. 풍속 민감도가 ±20도라 비교가 성립하지 않는다.
            # 2026-09-16 네 지점에서 MAE 7.36 → 2.36, point30 −10.84 → −3.55 로 줄었다.
            # 여기서는 엔진 Tmrt 를 **실측 기온·실측 풍속**으로 흑구온도까지 내려서
            # 실측 흑구온도와 직접 뺀다. 남는 것은 복사항이다.
            _k = 1.1e8 * _v ** 0.6 / (_eps * _D ** 0.4)
            _rhs = (float(est["mrt_globe"]) + 273.15) ** 4
            _lo = min(_ta, float(est["mrt_globe"])) - 30.0
            _hi = max(_ta, float(est["mrt_globe"])) + 30.0
            for _ in range(200):          # 좌변은 Tg 에 단조증가 → 이분법
                _m = (_lo + _hi) / 2.0
                if (_m + 273.15) ** 4 + _k * (_m - _ta) - _rhs > 0:
                    _hi = _m
                else:
                    _lo = _m
            est["tg_pred"] = round((_lo + _hi) / 2.0, 2)
            resid["tg"] = round(est["tg_pred"] - _tg, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    archive = getattr(request.app.state, "archive", None)
    if archive is not None:
        archive.record_field_check(lat=lat, lon=lon, meas=meas, est=est, note=note)

    return {"est": est, "residual": resid, "saved": archive is not None}


@router.get(
    "/building/risk",
    summary="건물 열취약 판정 — 건축물대장 연식·구조 기반 (실내축 2단계, 2026-08-10)",
)
async def building_risk_at(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    age: int | None = Query(None, ge=0, le=120),
    conditions: str | None = Query(None, description="취약군 콤마 구분: cardio,resp,…"),
    ambient: float | None = Query(None, description="방 센서 실측 실내온도(°C) — 있으면 추정 대체"),
    humidity: float | None = Query(
        None, ge=0.0, le=100.0,
        description="방 센서 실측 실내습도(%) — 있으면 외기습도 변환 대신 실측 사용 (2026-08-14)",
    ),
    floor: int | None = Query(None, ge=1, le=120, description="거주 층 — 최상층/중간층 보정"),
    facing: float | None = Query(
        None, ge=0.0, le=360.0,
        description="창문이 향하는 방위각(0=북,90=동) — 온보딩에서 받으면 건물 방위 추정보다 우선",
    ),
    wall: float | None = Query(None, ge=-30.0, le=80.0, description="벽 표면온도 실측(°C) — MLX90614"),
    radiant: float | None = Query(
        None, ge=-30.0, le=80.0, description="방 복사온도 실측(°C) — 8x8 배경 평균(사람 제외)"),
    occupied: bool | None = Query(None, description="8x8 재실 여부"),
    sensor_id: str | None = Query(
        None, max_length=64,
        description="방 센서 식별자 — 있으면 잔차를 좌표 대신 센서별로 학습 (집·연구실 구분, 2026-09-24)"),
    wall_exterior: bool = Query(
        False, description="벽면센서가 외벽 안쪽 면을 측정하는가 — 참이면 외피 표면온도 잔차 산출"),
    envelope_surface: float | None = Query(
        None, ge=-30.0, le=80.0, description="외피(창·외벽) 안쪽 표면 실측(°C) — 8x8 선택 칸 평균"),
    envelope_type: str | None = Query(None, pattern="^(glass|wall)$", description="glass / wall"),
) -> dict:
    """좌표의 건물 정보 + **실내 체감기후(실내 pVPTI)** 를 반환.

    야외 pVPTI와 같은 사상 — 건물 축열·시간대별 일사·구름으로 실내 기온을 추정하고,
    습도(후덥지근함)·무풍을 반영한 '실내 체감'과 취약군 반영 위험 등급을 낸다.
    이어러블 실측(ambient)이 오면 추정 대신 실측 기반 — '이 사람이 실제 겪는 실내 체감'.
    데이터: V-World + 건축물대장 + 기상청 — 공개 데이터만, 좌표·생체 미저장.
    """
    from app.services.building import building_risk, to_dict
    from app.services.indoor import compute_indoor, forecast_indoor_periods, hvac_plan

    b = await building_risk(lat, lon)
    # 2026-08-18 — 건물 정보가 없다고 **404로 끊지 않는다.**
    # 실내 체감의 주 입력은 외기 기온·일평균·일사·습도이고 건축물대장은 보정 항이다.
    # 대장이 안 잡히는 곳(산번지·무허가·옥탑·컨테이너·신축 미등재)이야말로 폭염에
    # 취약한데, 지금까지는 그런 자리에서 실내 축이 통째로 죽었다(앱에는 "서버 응답
    # 실패"로만 보였다). 표준 건물로 가정해서라도 값을 낸다.
    if b is None:
        result = {
            "address": None, "building_name": None, "built_year": None,
            "floors": None, "structure": None, "roof": None, "purpose": None,
            "score": 0, "level": None,
            "reasons": ["건축물대장을 찾지 못해 표준 건물로 가정해 추정했어요"],
        }
    else:
        result = to_dict(b)
    result["building_found"] = b is not None
    result["est_indoor_c"] = None
    result["indoor_pvpti"] = None
    result["indoor_risk"] = None
    result["indoor_measured"] = False

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None:
        try:
            obs = await orchestrator.kma.get_current_observation(lat, lon)
            # 오늘 평균기온(축열 기준) — 실패 시 현재기온
            #
            # ⚠️ 2026-08-14 버그 수정. 전에는 오늘 예보 슬롯의 산술평균을 썼는데,
            #    단기예보는 **발표 시각 이후의 미래 슬롯만** 준다. 새벽 06시에 조회하면
            #    06~23시만 남아 밤·새벽의 낮은 기온이 통째로 빠지고 낮 기온만 평균에
            #    들어간다 → t_mean 과대 → 실내 기온 추정 과대.
            #    (8/14 06:12 부산: 역산 t_mean 31.0°C, 실제 일평균은 27°C 안팎)
            #    조회가 이를수록 오차가 크고 저녁엔 반대로 작아지는 —
            #    "낮·아침 검증에서는 안 보이는" 종류의 버그였다.
            #
            #    수정: 기후학 표준인 일평균 =(일최고+일최저)/2 로 계산하고,
            #    이미 지나간 시간대를 대표하도록 **현재 관측값을 후보에 포함**한다
            #    (새벽이면 현재값이 그날 최저에 가깝다).
            t_mean = obs.temperature_c
            fcst = None
            geom = None
            try:
                fcst = await orchestrator.kma.get_short_term_forecast(lat, lon)
                today = [
                    f.temperature_c for f in fcst
                    if f.forecast_for.date() == datetime.now(KST).date()
                    and f.temperature_c is not None
                ]
                if today:
                    vals = today + [obs.temperature_c]
                    t_mean = (max(vals) + min(vals)) / 2.0
            except Exception:  # noqa: BLE001
                pass
            # 구름량 — 초단기예보 SKY (엔진 일사감쇠와 동일 소스), 실패 시 중간값
            cloud = 0.5
            try:
                wx = WeatherContext(
                    temperature_c=obs.temperature_c,
                    humidity_pct=obs.humidity_pct,
                    wind_speed_ms=obs.wind_speed_ms,
                    wind_direction_deg=obs.wind_direction_deg,
                    precipitation_mm=obs.precipitation_mm,
                )
                sky = await orchestrator._get_sky_code(lat, lon, wx)  # noqa: SLF001
                cloud = {1: 0.1, 3: 0.6, 4: 0.9}.get(sky or 0, 0.5)
            except Exception:  # noqa: BLE001
                pass

            # 건물 방위(GIS) — 서향/북향 세대의 일사 취득 차이를 반영 (2026-08-14).
            # 건물은 안 변하므로 30일 캐시. 못 구하면 gain=1.0이라 기존과 동일하게 동작.
            facade_gain, facade_note = 1.0, None
            try:
                from app.core.smti import compute_solar_position
                from app.services.geo import (
                    building_geometry, facade_solar_gain, shading_factor,
                )

                sun = compute_solar_position(lat, lon, datetime.now(KST))
                # 건축물대장에서 이미 아는 건물명·층수로 교차 대조 — 긴 아파트 옆의
                # 작은 상가가 '가장 가까운 건물'로 잡히는 것을 막는다 (2026-08-15 실검증).
                geom = await building_geometry(
                    lat, lon,
                    name_hint=b.building_name if b else None,
                    floors_hint=b.floors if b else None,
                )
                facade_gain, facade_note = facade_solar_gain(
                    sun.azimuth_deg, sun.elevation_deg, geom, facing_deg=facing,
                )
                # 이웃 건물 차폐(실내판 SVF) — 사용자 층 높이 기준 (2026-08-15).
                # 저층은 옆 동 그림자에 자주 들어가고 고층은 벗어난다.
                shade_gain, shade_note = shading_factor(
                    sun.azimuth_deg, sun.elevation_deg, geom, floor,
                )
                facade_gain *= shade_gain
                if shade_note:
                    facade_note = f"{facade_note} · {shade_note}" if facade_note else shade_note
            except Exception as e:  # noqa: BLE001
                logger.warning(f"facade gain skipped ({type(e).__name__}): {e}")

            # BTLI 존 컨텍스트 (2026-09-24) — 외부부하를 VPTI 엔진(FSI·EMTI·FWI·음영)으로.
            #   층 높이 SVF(GIS 광선투사) + VPTI 공간지표(GVI·BVI) + 외피 재질(구조) + 이웃 음영.
            #   실패하면 None → 기존 휴리스틱 경로로 자동 폴백.
            zone = None
            try:
                from app.services.btli_zone import build_zone_context
                zone = await build_zone_context(
                    lat=lat, lon=lon, floor=floor, facing_deg=facing,
                    structure=b.structure if b else None, geom=geom,
                    orchestrator=orchestrator,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"btli zone skipped ({type(e).__name__}): {e}")

            # 열 기억 (2026-08-16) — 좌표·층별 마지막 실내값을 기억해 급변 날씨에서
            # 방이 즉시 리셋되지 않게 한다. 실측(센서)이 있었으면 그 값이 기억되므로
            # 센서가 잠시 끊겨도 한동안 실측 수준을 유지한다.
            # ⚠️ 워커별 인메모리 — 워커마다 따로 수렴하고 재시작 시 사라짐(그 경우
            #    공식값으로 폴백). 정밀화는 센서 짝 데이터로 τ 교정과 함께.
            mem_key = (round(lat, 4), round(lon, 4), floor or 0)
            prev_rec = _INDOOR_MEMORY.get(mem_key)
            t_in_prev = prev_age_h = None
            if prev_rec is not None:
                age_h = (_time.time() - prev_rec[0]) / 3600.0
                if age_h < 48.0:
                    t_in_prev, prev_age_h = prev_rec[1], age_h

            # 잔차 학습 (2026-09-23) — 실측 − 외부부하 추정을 집(좌표·층)별 지수평균.
            #   센서가 있으면 갱신만, 없으면(끊김·다른 방) 7일 안의 값을 추정에 더한다.
            # 2026-09-24: 센서 식별자가 오면 센서별로 학습 — 같은 폰이 집·연구실 센서를
            #   오가도 서로의 잔차가 섞이지 않는다 (좌표는 GPS 흔들림에도 약하다).
            res_key = ("sensor", sensor_id) if sensor_id else mem_key
            res_rec = _INDOOR_RESIDUAL.get(res_key)
            residual_bias = None
            if res_rec is not None and (_time.time() - res_rec[0]) < 7 * 86400:
                residual_bias = res_rec[1]

            ind = compute_indoor(
                t_out_now=obs.temperature_c,
                t_mean_today=t_mean,
                humidity_pct=obs.humidity_pct,
                cloud_fraction=cloud,
                building_score=b.score if b else 0,
                structure=b.structure if b else None,
                age=age,
                conditions=conditions.split(",") if conditions else None,
                ambient_measured=ambient,
                humidity_measured=humidity,
                floor=floor,
                total_floors=b.floors if b else None,
                lat=lat,          # 실제 태양 위치로 일사 계산 (2026-08-14)
                lon=lon,
                facade_gain=facade_gain,
                facade_note=facade_note,
                t_in_prev=t_in_prev,
                prev_age_h=prev_age_h,
                wall_measured=wall,
                radiant_measured=radiant,
                occupied=occupied,
                residual_bias=residual_bias,
                zone=zone,
                wind_ms=obs.wind_speed_ms,
                wind_dir_deg=obs.wind_direction_deg,
                wall_exterior=wall_exterior,
                envelope_surface=envelope_surface,
                envelope_type=envelope_type,
            )
            if ind.residual is not None:
                ema = ind.residual if res_rec is None else 0.8 * res_rec[1] + 0.2 * ind.residual
                if len(_INDOOR_RESIDUAL) > 10_000:
                    _INDOOR_RESIDUAL.clear()
                _INDOOR_RESIDUAL[res_key] = (_time.time(), ema)
            if len(_INDOOR_MEMORY) > 10_000:      # 폭주 방지 — 오래된 것부터 비움
                _INDOOR_MEMORY.clear()
            _INDOOR_MEMORY[mem_key] = (_time.time(), ind.t_in_est)
            result["est_indoor_c"] = ind.t_in_est
            result["indoor_pvpti"] = ind.indoor_pvpti
            result["indoor_risk"] = ind.indoor_risk
            result["indoor_measured"] = ind.measured
            result["indoor_basis"] = ind.basis
            result["indoor_state"] = ind.basis.get("indoor_state")
            result["indoor_note"] = _indoor_note(ind.measured, ind.basis)

            # 벽면센서 실측 기록 (2026-09-24) — 실측이 있을 때만. 실패해도 응답엔 영향 없음.
            _arch = getattr(request.app.state, "archive", None)
            if _arch is not None and ambient is not None:
                try:
                    bs0 = ind.basis
                    bt = bs0.get("btli") or {}
                    st = bs0.get("indoor_state") or {}
                    _arch.record_indoor(
                        sensor_id=sensor_id, lat=lat, lon=lon, floor=floor, facing=facing,
                        air_c=ambient, rh=humidity, wall_c=wall, radiant_c=radiant,
                        envelope_c=envelope_surface, envelope_type=envelope_type,
                        occupied=occupied, t_out=obs.temperature_c, rh_out=obs.humidity_pct,
                        wind_ms=obs.wind_speed_ms, cloud=cloud,
                        t_in_formula=bs0.get("t_in_formula"), btli_k=bt.get("btli_k"),
                        i_face=bt.get("i_face_wm2"), h_out=bt.get("fwi_h_out"),
                        t_si_pred=bs0.get("t_si_pred"), residual=bs0.get("residual"),
                        surface_resid=bs0.get("surface_residual"),
                        t_operative=bs0.get("t_operative"), feel=ind.indoor_pvpti,
                        risk=ind.indoor_risk, state=st.get("code"), basis=bs0,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"indoor archive skipped ({type(e).__name__}): {e}")

            # 실내 체감 시간대 예보 (2026-09-24) — 외부부하 예보 + 이 방 실측 잔차
            try:
                if fcst:
                    _sky = {"맑음": 0.1, "구름많음": 0.6, "흐림": 0.9}
                    hours = [
                        (f.forecast_for, f.temperature_c,
                         f.humidity_pct if f.humidity_pct is not None else obs.humidity_pct,
                         _sky.get(f.sky_condition or "", 0.5),
                         f.wind_speed_ms if f.wind_speed_ms is not None else obs.wind_speed_ms,
                         f.wind_direction_deg if f.wind_direction_deg is not None
                         else obs.wind_direction_deg)
                        for f in fcst if f.temperature_c is not None
                    ]
                    by_date: dict = {}
                    for f in fcst:
                        if f.temperature_c is not None:
                            by_date.setdefault(f.forecast_for.date(), []).append(f.temperature_c)
                    by_date.setdefault(datetime.now(KST).date(), []).append(obs.temperature_c)
                    t_mean_by_date = {d: (max(v) + min(v)) / 2.0 for d, v in by_date.items()}

                    def _gain_at(dt, _geom=geom):
                        if _geom is None:
                            return facade_gain
                        try:
                            from app.core.smti import compute_solar_position
                            from app.services.geo import facade_solar_gain, shading_factor
                            s_ = compute_solar_position(lat, lon, dt)
                            g_, _n = facade_solar_gain(
                                s_.azimuth_deg, s_.elevation_deg, _geom, facing_deg=facing)
                            sh_, _n2 = shading_factor(s_.azimuth_deg, s_.elevation_deg, _geom, floor)
                            return g_ * sh_
                        except Exception:  # noqa: BLE001
                            return facade_gain

                    bs = ind.basis
                    _hourly: list = []
                    rad_off = (bs["t_radiant"] - ind.t_in_est) if bs.get("t_radiant") is not None else None
                    result["indoor_forecast"] = forecast_indoor_periods(
                        now=datetime.now(KST),
                        hours=hours,
                        t_mean_by_date=t_mean_by_date,
                        base=dict(
                            building_score=b.score if b else 0,
                            structure=b.structure if b else None,
                            age=age,
                            conditions=conditions.split(",") if conditions else None,
                            floor=floor, total_floors=b.floors if b else None,
                            lat=lat, lon=lon, zone=zone,
                        ),
                        gain_at=_gain_at,
                        formula_now=bs.get("t_in_formula"),
                        t_in_now=ambient,
                        rh_in_now=humidity,
                        rad_offset=rad_off,
                        residual_bias=residual_bias,
                        hourly_out=_hourly,
                    )
                    result["indoor_hvac_plan"] = hvac_plan(_hourly, datetime.now(KST))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"indoor forecast failed ({type(e).__name__}): {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"indoor pvpti failed: {e}")
    return result


@router.get("/indoor/sensors", summary="벽면센서 목록·기록 수 (관리자용)")
async def indoor_sensors(request: Request, x_field_key: str | None = Header(None)) -> dict:
    _require_field_key(x_field_key)
    archive = getattr(request.app.state, "archive", None)
    if archive is None:
        return {"enabled": False, "sensors": []}
    return {"enabled": True, "sensors": await archive.indoor_sensors()}


@router.get("/indoor/sensor-log", summary="벽면센서 실측+BTLI 기록 CSV (관리자용)")
async def indoor_sensor_log(
    request: Request,
    sensor_id: str | None = Query(None, max_length=64),
    hours: int = Query(24 * 7, ge=1, le=24 * 365),
    x_field_key: str | None = Header(None),
    key: str | None = Query(None, description="브라우저 내려받기용 — 헤더 대신"),
):
    """논문·교정용 원자료. 헤더 X-Field-Key 또는 ?key= 로 인증."""
    _require_field_key(x_field_key or key)
    archive = getattr(request.app.state, "archive", None)
    rows = await archive.indoor_log(sensor_id, hours) if archive is not None else []
    import csv as _csv
    import io as _io
    from fastapi.responses import Response as _Resp
    buf = _io.StringIO()
    cols = archive.INDOOR_COLS if archive is not None else ()
    w = _csv.writer(buf)
    w.writerow(cols)
    # 시각은 한국 시간(KST, +09:00)으로 내보낸다 (2026-09-24). DB 저장은 UTC 그대로.
    def _cell(v):
        if isinstance(v, datetime):
            return (v.astimezone(KST) if v.tzinfo else v).isoformat(timespec="seconds")
        return v
    for r in rows:
        w.writerow([_cell(r[c]) for c in cols])
    fn = f"indoor_sensor_{(sensor_id or 'all')[:8]}_{hours}h.csv"
    return _Resp(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                 headers={"Content-Disposition": f'attachment; filename="{fn}"'})


def _require_field_key(x_field_key: str | None) -> None:
    """관리자 전용 엔드포인트 공통 관문 — 키가 없거나 틀리면 존재 자체를 숨긴다(404)."""
    s = get_settings()
    if not s.field_key or x_field_key != s.field_key:
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/archive/stats", summary="측정 이력 적재 현황 (관리자용)")
async def archive_stats(
    request: Request,
    x_field_key: str | None = Header(None),
) -> dict:
    """데이터가 실제로 쌓이고 있는지 확인 — 건수·기간·격자 수. (X-Field-Key 필요, 2026-09-04)"""
    _require_field_key(x_field_key)
    archive = getattr(request.app.state, "archive", None)
    if archive is None:
        return {"enabled": False, "reason": "archive 미초기화"}
    return await archive.stats()


@router.get("/archive/hotspots", summary="격자별 체감기후 집계 (핫스팟)")
async def archive_hotspots(
    request: Request,
    hours: int = Query(24, ge=1, le=24 * 90, description="최근 N시간"),
    min_samples: int = Query(1, ge=1, le=100, description="이 표본 수 미만 격자는 제외"),
    limit: int = Query(500, ge=1, le=5000),
    x_field_key: str | None = Header(None),
) -> dict:
    """개인 식별자 없이, 격자 단위 평균·최고 체감온도를 반환한다.

    지자체 제안·연구용 집계. min_samples 를 올리면 표본이 적은 격자를 제외해
    재식별 위험을 낮출 수 있다(대외 제공 시 권장).
    영업비밀(1급 수집 데이터)이라 X-Field-Key 없이는 404 (2026-09-04).
    """
    _require_field_key(x_field_key)
    archive = getattr(request.app.state, "archive", None)
    if archive is None:
        return {"cells": [], "note": "archive 미초기화"}
    cells = await archive.hotspots(hours=hours, min_samples=min_samples, limit=limit)
    return {"hours": hours, "min_samples": min_samples, "count": len(cells), "cells": cells}


@router.get("/archive/dashboard", include_in_schema=False)
async def archive_dashboard_page():
    """데이터 자산 대시보드 페이지 (대표 전용, 2026-09-04) — 서버가 직접 서빙.

    /api/v1/field 와 같은 방식: 페이지 자체는 공개돼도 무해(숫자가 없음),
    데이터(/archive/dashboard_data)는 X-Field-Key 없이는 404.
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    p = Path(__file__).resolve().parents[1] / "web" / "archive_dashboard.html"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(p, media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/archive/dashboard_data", include_in_schema=False)
async def archive_dashboard_data(
    request: Request,
    hours: int = Query(24 * 7, ge=1, le=24 * 365),
    min_samples: int = Query(1, ge=1, le=100),
    x_field_key: str | None = Header(None),
) -> dict:
    _require_field_key(x_field_key)
    archive = getattr(request.app.state, "archive", None)
    if archive is None:
        return {"enabled": False, "reason": "archive 미초기화"}
    return await archive.dashboard(hours=hours, min_samples=min_samples)


@router.get("/archive/whatif", include_in_schema=False)
async def archive_whatif(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    hours: int = Query(24 * 30, ge=1, le=24 * 365),
    x_field_key: str | None = Header(None),
) -> dict:
    """격자 개선 시뮬레이션 (대표 전용, 2026-09-05) — 가로수·그늘막·차열포장 적용 시 pVPTI 변화."""
    _require_field_key(x_field_key)
    from app.services.whatif import cell_whatif
    return await cell_whatif(getattr(request.app.state, "archive", None), lat, lon, hours,
                             orchestrator=getattr(request.app.state, "orchestrator", None))


@router.get("/archive/validate", include_in_schema=False)
async def archive_validate(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    when: str = Query(..., description="ISO 시각 (KST 가정)"),
    ta: float = Query(...), rh: float = Query(...), wind: float = Query(0.0),
    cloud: float = Query(0.0, ge=0, le=1),
    mrt_obs: float | None = Query(None), pet_obs: float | None = Query(None),
    wbgt_obs: float | None = Query(None), place: str | None = Query(None),
    svf_obs: float | None = Query(None), gvi_obs: float | None = Query(None),
    bvi_obs: float | None = Query(None), ts_obs: float | None = Query(None),
    inject: bool = Query(False, description="실측 SVF/GVI 주입(3단 분해)"),
    save: bool = Query(False),
    x_field_key: str | None = Header(None),
) -> dict:
    """현장 실측 검증 — 측정 순간 조건으로 엔진 MRT·pVPTI 예측 (대표 전용, 2026-09-05).

    save=true 이면 실측·엔진 짝을 field_check 테이블에 적재(대시보드 검증 카드용)."""
    _require_field_key(x_field_key)
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        return {"ok": False, "reason": "엔진 미기동"}
    try:
        w = _dt.fromisoformat(when)
        if w.tzinfo is None:
            w = w.replace(tzinfo=_tz(_td(hours=9)))   # KST
    except ValueError:
        return {"ok": False, "reason": "when 형식 오류(ISO)"}
    try:
        r = await orch.validate_at(lat, lon, w, ta, rh, wind, cloud,
                                   svf_obs=(svf_obs if inject else None),
                                   gvi_obs=(gvi_obs if inject else None),
                                   bvi_obs=(bvi_obs if inject else None))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    if save:
        archive = getattr(request.app.state, "archive", None)
        if archive is not None:
            archive.record_field_check(
                lat=lat, lon=lon,
                meas={"ta": ta, "rh": rh, "wind_ms": wind,
                      "mrt": mrt_obs, "pet": pet_obs, "wbgt": wbgt_obs,
                      "source": "field_20260820_csv"},
                est={"pvpti": r["pvpti"], "mrt": r["mrt"], "svf": r["svf"],
                     "gvi": r["gvi"], "shade": r["shade"]},
                note=place, observed_at=w,
            )
            r["saved"] = True
    return {"ok": True, **r}


@router.get("/archive/siteplan", include_in_schema=False)
async def archive_siteplan(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    level: int = Query(19, ge=15, le=20), size: int = Query(800, ge=300, le=1024),
    x_field_key: str | None = Header(None),
) -> dict:
    """개선 시뮬 배치도 바탕 — 위성사진 + 축척 + 보행 축 (대표 전용, 2026-09-05)."""
    _require_field_key(x_field_key)
    from app.services.siteplan import siteplan
    return await siteplan(getattr(request.app.state, "archive", None), lat, lon, level, size)


@router.get("/archive/urban_whatif", include_in_schema=False)
async def archive_urban_whatif(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    d_svf: float = Query(0.0, ge=-1, le=1),
    d_gvi: float = Query(0.0, ge=-1, le=1),
    d_bvi: float = Query(0.0, ge=-1, le=1),
    ground_mat: str = Query("base"),
    direct_shade: float = Query(1.0, ge=0, le=1),
    x_field_key: str | None = Header(None),
) -> dict:
    """도시계획 재개발 what-if — 건물/지면재질/녹지 변경 시 보행자 체감 변화 (대표 전용, 2026-09-05)."""
    _require_field_key(x_field_key)
    from app.services.whatif import area_whatif
    levers = {"d_svf": d_svf, "d_gvi": d_gvi, "d_bvi": d_bvi,
              "ground_mat": ground_mat, "direct_shade": direct_shade}
    return await area_whatif(getattr(request.app.state, "archive", None),
                             [{"lat": lat, "lon": lon}], levers)


@router.get("/archive/btli", include_in_schema=False)
async def archive_btli(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    material_new: str = Query("coolpaint"),
    footprint_m2: float = Query(600.0, ge=30, le=100000),
    floors: int | None = Query(None, ge=1, le=200),
    x_field_key: str | None = Header(None),
) -> dict:
    """BTLI 외피 열부하 what-if — 외피 재질 교체 시 냉방부하 delta% (대표 전용, 2026-09-05).

    floors 미지정 시 건축물대장(building_risk)에서 자동 조회."""
    _require_field_key(x_field_key)
    from app.services.btli import facade_load, FACADE_PRESETS
    if material_new not in FACADE_PRESETS:
        return {"ok": False, "reason": f"미등록 재질: {material_new}"}
    structure = None
    bname = None
    if floors is None:
        try:
            from app.services import building as _b
            br = await _b.building_risk(lat, lon)
            if br is not None:
                floors = br.floors or floors
                structure = br.structure
                bname = br.building_name
        except Exception:  # noqa: BLE001
            pass
    floors = floors or 15
    r = facade_load(lat=lat, lon=lon, footprint_area_m2=footprint_m2,
                    floors=floors, structure=structure, material_new=material_new)
    r["building_name"] = bname
    # 옥외 짝: 외피 재질 → 보행자 체감(pVPTI) 영향 (그 지점 SVF/GVI/BVI 필요)
    try:
        from app.services.btli import facade_outdoor_delta
        orch = getattr(request.app.state, "orchestrator", None)
        sp = await orch.spatial_at(lat, lon) if orch is not None else None
        if sp:
            r["outdoor"] = facade_outdoor_delta(
                lat=lat, lon=lon, svf=sp["svf"], gvi=sp["gvi"], bvi=sp["bvi"],
                material_new=material_new)
            r["outdoor"]["svf"] = round(sp["svf"], 2); r["outdoor"]["bvi"] = round(sp["bvi"], 2)
        else:
            r["outdoor"] = {"ok": False, "reason": "이 지점 SVF 없음"}
    except Exception as e:  # noqa: BLE001
        r["outdoor"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    return r


@router.get("/archive/geo_svf", include_in_schema=False)
async def archive_geo_svf(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    x_field_key: str | None = Header(None),
) -> dict:
    """진단 — 건물 GIS 기하만으로 SVF 산출 (스트리트뷰 없이). 실측 대조용."""
    _require_field_key(x_field_key)
    from app.services.geo import svf_geometric
    try:
        r = await svf_geometric(lat, lon)
        r["ok"] = True; r["lat"] = lat; r["lon"] = lon
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


@router.get("/geo/form", summary="좌표의 공간 형태 — 기하 SVF·가로폭·협곡비 (건물GIS만, 키 불필요)")
async def geo_form(lat: float = Query(...), lon: float = Query(...)) -> dict:
    """정적 공간형태만 반환(날씨·위성 호출 없음). 몸씨 기록 좌표의 골목/폭 분포 집계용 (2026-09-10)."""
    from app.services.geo import svf_geometric, street_width_geometric
    try:
        s = await svf_geometric(lat, lon)
        w = await street_width_geometric(lat, lon)
        return {"ok": True, "lat": lat, "lon": lon, "svf": s.get("svf"),
                "n_buildings": s.get("n_buildings"), "svf_source": s.get("source"),
                "street_width_m": w.get("width_m"), "hw_ratio": w.get("hw_ratio"),
                "street_axis_deg": w.get("axis_deg"), "snapped_m": w.get("snapped_m")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def _wbgt_fields(ta: float, rh: float, v: float, mrt: float) -> dict:
    from vpti_core.wbgt import wbgt_outdoor, wbgt_level
    w = wbgt_outdoor(ta, rh, v, mrt)
    return {"wbgt": w["wbgt"], "wbgt_level": wbgt_level(w["wbgt"]),
            "globe_c": w["globe_c"], "wet_bulb_c": w["wet_bulb_c"]}


def _paw_risk(ground_c: float) -> dict:
    """노면온도 → 강아지 발바닥 위험. 일본에서 肉球やけど 인식이 높아 이 한 줄이 설치 이유가 된다.
    기준: 수의 가이드 통용치(아스팔트 52°C 에서 1분 내 화상, 60°C 는 수 초)."""
    if ground_c >= 60.0:
        return {"code": "danger", "ja": "危険・散歩は避けて", "ko": "위험 · 산책 금지", "en": "Dangerous"}
    if ground_c >= 52.0:
        return {"code": "burn", "ja": "肉球やけどの恐れ", "ko": "발바닥 화상 주의", "en": "Burn risk"}
    if ground_c >= 45.0:
        return {"code": "hot", "ja": "熱い・短時間で", "ko": "뜨거움 · 짧게", "en": "Hot"}
    return {"code": "ok", "ja": "問題なし", "ko": "괜찮음", "en": "OK"}


async def _geo_vpti_compute(lat: float, lon: float) -> dict:
    """GSV 없이 좌표 → 완전한 체감(VPTI). 기하 SVF(사전적재 건물)+기하 그늘+Open-Meteo
    날씨+Sentinel-2 위성 GVI+교정엔진(compute_vpti_thermal, UTCI/PET). 전세계 파일럿.
    /archive/geo_vpti(진단)와 /vpti/geo/at(앱)가 공유한다.
    """
    from datetime import datetime, timezone
    from app.services.geo import (svf_geometric, sun_blocked_outdoor,
                                  dominant_wall_material, street_width_geometric)
    from app.services.open_meteo import get_current_observation
    from app.services.sentinel_hub import (get_surface as _get_surface,
        ndvi_to_gvi as _ndvi_to_gvi, surface_to_materials as _surf_to_mats)
    from vpti_core import estimate_solar, DEFAULT_CONFIG
    from vpti_core.vsi import ViewSegmentation
    from vpti_core.smti import MaterialFraction
    from vpti_core.vpti import WeatherContext, compute_vpti_thermal

    # 동시 실행 (2026-09-16). 점 조회가 3.4초였다. 여섯 가지를 **순차로** 기다리고 있었는데
    # 서로 의존하지 않는다: 기하(SVF·가로폭·그늘·벽재질)는 건물 링만 쓰고, 날씨는 Open-Meteo,
    # 지표는 Sentinel Hub 다. 태양 위치만 먼저 있으면 되고 그건 계산이라 즉시 나온다.
    # 합계가 아니라 **가장 느린 하나**만큼만 걸리게 한다. 순서를 바꾼 게 아니라 기다림을 겹쳤을 뿐이라
    # 결과값은 달라지지 않는다.
    now = datetime.now(timezone.utc)
    sol = estimate_solar(lat, lon, now, config=DEFAULT_CONFIG.solar)

    async def _safe(coro, dflt):
        try:
            return await coro
        except Exception:  # noqa: BLE001
            return dflt

    _t0 = _time.perf_counter()
    (svf_r, _sw, obs, _shade, surface, wall_mat) = await asyncio.gather(
        svf_geometric(lat, lon),
        street_width_geometric(lat, lon),
        get_current_observation(lat, lon),
        sun_blocked_outdoor(lat, lon, sol.solar_azimuth_deg, sol.solar_elevation_deg),
        _safe(_get_surface(lat, lon), None),
        dominant_wall_material(lat, lon),
    )
    _par_ms = round((_time.perf_counter() - _t0) * 1000)

    if svf_r.get("svf") is None:
        return {"ok": False, "reason": svf_r.get("reason", "SVF 없음"),
                "svf": None, "lat": lat, "lon": lon}
    svf = float(svf_r["svf"])
    blocked, shade_note = _shade
    # 가로수 그늘 (2026-09-12): 건물이 안 막아도 태양 방향에 나무가 있으면 직사광이 줄어든다.
    # NDVI 로 뭉개지 않고 OSM 개별 나무 좌표로만 판정한다 — 공원 안 뙤약볕 길을 그늘이라 하지 않기 위해.
    # 한국은 OFF (2026-09-14 실측 근거는 config.geo_tree_shade 주석 참조), 일본은 ON.
    # 지역 판정은 거친 경위도 상자다 — 대마도·규슈 이남과 동경 130.9도 이동을 일본으로 본다.
    # 부산(129.1E, 35.1N)·제주(126.5E)는 제외된다.
    _jp = (lon >= 130.9) or (lon >= 129.2 and lat <= 34.3)
    _use_tree = (get_settings().geo_tree_shade_jp if _jp
                 else get_settings().geo_tree_shade)
    tree_f = 0.0
    if not blocked and _use_tree:
        try:
            from app.services.geo import tree_shade_factor
            tree_f = await tree_shade_factor(lat, lon, sol.solar_azimuth_deg, sol.solar_elevation_deg)
        except Exception:  # noqa: BLE001
            tree_f = 0.0
    direct_shade = 0.0 if blocked else (1.0 - tree_f)
    night = sol.solar_elevation_deg <= 0.0
    exposure = ("야간" if night else
                ("그늘" if blocked else ("나무그늘" if tree_f >= 0.4 else "양지")))

    gvi = 0.0
    gvi_src = "none"
    if surface is not None:
        gvi = _ndvi_to_gvi(surface["ndvi"]); gvi_src = "sentinel2-ndvi"

    # 스칼라 SVF/GVI → 5-view 합성(up.sky=SVF, 수평.sky=SVF/2 로 reconstruct_svf 가 원래 SVF
    # 복원, 수평.veg=GVI) → compute_vpti_thermal 전체 물리 경로.
    g = max(0.0, min(1.0, gvi))
    b = max(0.0, min(1.0 - g, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    views = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                              vegetation_ratio=0.0, building_ratio=0.0)]
    views += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=g,
                               building_ratio=b)
              for d in ("front", "back", "left", "right")]
    # 지표 재질 — 위성(NDVI/NDWI/알베도)→SMTI 재질 분율. 실패 시 기본(unknown).
    mat_src = "default"
    if surface is not None:
        _pairs, _ga = _surf_to_mats(surface)
        mats = [MaterialFraction(material=m, fraction=f) for m, f in _pairs]
        mat_src = "sentinel2"
    else:
        mats = [MaterialFraction(material="unknown", fraction=1.0)]
    wc = WeatherContext(temperature_c=obs.temperature_c, humidity_pct=obs.humidity_pct,
                        wind_speed_ms=obs.wind_speed_ms,
                        wind_direction_deg=obs.wind_direction_deg)
    # 측면 벽 온도 — sunlit 벽 복사 반영(2026-09-09 v1). 낮 + 어느정도 둘러싸임(svf<0.92)일 때만.
    # ⚠️ v1: 벽 재질 콘크리트 기본(alb0.30), sunlit_frac 0.45 근사. 방위 정밀화·PLATEAU 재질은 다음 단계.
    wall_temp = None        # wall_mat 은 위 gather 에서 이미 받았다
    if get_settings().geo_wall_stage and svf < 0.92:   # 벽 스테이지(기본 OFF, 벽면센서 교정 후 ON)
        from vpti_core.mrt import (estimate_wall_temp, estimate_wall_temp_transient,
                                   sky_emissivity)
        from app.services.open_meteo import get_hourly_air_series
        _epsk = sky_emissivity(obs.temperature_c, obs.humidity_pct,
                               sol.cloud_fraction, DEFAULT_CONFIG.mrt)
        _a, _e, _hc = wall_mat["albedo"], wall_mat["emissivity"], wall_mat.get("hc", 100000.0)
        # 과거 12h forcing(시간별 기온 + 시간별 기하 일사·방위) → 열질량 과도 벽온도
        _series = []
        try:
            for _age, _ta in await get_hourly_air_series(lat, lon, 12):
                _sh = estimate_solar(lat, lon, now - timedelta(seconds=_age),
                                     config=DEFAULT_CONFIG.solar)
                _series.append((_age, _ta, _sh.dni, _sh.dhi,
                                _sh.solar_elevation_deg, _sh.solar_azimuth_deg))
        except Exception:  # noqa: BLE001
            _series = []
        if _series:
            _series.append((0.0, obs.temperature_c, sol.dni, sol.dhi,
                            sol.solar_elevation_deg, sol.solar_azimuth_deg))
            # look 방향 d 가 마주보는 파사드 법선 = d방위 + 180 (동향벽=아침, 서향벽=오후)
            _FN = {"N": 180.0, "E": 270.0, "S": 0.0, "W": 90.0}
            _wd = {}
            for _d, _fn in _FN.items():
                _wt = estimate_wall_temp_transient(
                    _series, _a, _e, 0.45, obs.wind_speed_ms, _epsk, _hc,
                    DEFAULT_CONFIG.mrt, facade_normal_deg=_fn)
                if _wt is not None:
                    _wd[_d] = _wt
            if _wd:
                wall_temp = _wd
        if wall_temp is None and sol.solar_elevation_deg > 0.0:   # 시리즈 없으면 정상상태
            wall_temp = estimate_wall_temp(obs.temperature_c, sol, _a, _e, 0.45,
                                           obs.wind_speed_ms, _epsk, DEFAULT_CONFIG.mrt)
    # 벽 단파반사 — 벽온도 스테이지(OFF)와 무관하게 동작한다. wall_albedo=None 이면 예전 계산 그대로.
    _wall_alb = wall_mat["albedo"] if get_settings().geo_wall_reflect else None
    r = compute_vpti_thermal(views_5=views, materials=mats, weather=wc,
                             road_axis_deg=0.0, lat=lat, lon=lon, when=now,
                             direct_shade=direct_shade, wall_temp_c=wall_temp,
                             wall_albedo=_wall_alb)
    # PET 잔차 AI — 물리 뼈대 위 학습 보정(부산 실측 80점, LOSO 2.66). 분포 밖이면 물리 폴백.
    from vpti_core.comfort import compute_pet
    from vpti_core.pet_residual import apply_pet_residual
    _pet_phys = float(compute_pet(tdb=obs.temperature_c, tr=float(r.mrt.tmrt),
                                  v=r.pedestrian_wind_ms, rh=obs.humidity_pct,
                                  season=r.season, config=DEFAULT_CONFIG.comfort).value)
    _feats = {"볕": direct_shade, "tier3_svf": svf, "tier3_gvi": gvi,
              "Ta": obs.temperature_c, "RH": obs.humidity_pct, "v": r.pedestrian_wind_ms,
              "태양고도": sol.solar_elevation_deg, "run_C_Tmrt": float(r.mrt.tmrt),
              "run_C_PET": _pet_phys,
              "ndvi30": (surface["ndvi"] if surface is not None else None)}
    _pet_ai, _ai_on, _ai_conf = apply_pet_residual(_pet_phys, _feats)
    cat = str(r.stress_category)
    direction = "heat" if "heat" in cat else ("cold" if "cold" in cat else "neutral")
    return {
        "ok": True, "lat": lat, "lon": lon,
        "vpti": round(float(r.vpti), 1),
        "risk": str(r.risk_level),
        "stress_category": cat,
        "stress_direction": direction,   # heat/cold/neutral — UI 색상 방향
        "comfort_index": r.comfort_index,
        "pet_physics": round(_pet_phys, 1),          # 물리 엔진 PET
        "pet_ai": round(_pet_ai, 1),                 # 잔차 AI 보정 PET(분포 밖이면 =물리)
        # 관용 이름 (2026-09-16) — 앱이 `pet` / `feels_like_c` 를 읽는데 응답에 그 키가 없어서
        # 화면에 계속 None 이 떴다. 값은 처음부터 계산되고 있었고 **이름만 달랐다.**
        # 앱을 고치는 대신 여기서 같이 내보낸다 — 다른 클라이언트도 안 깨진다.
        # 대표값은 **AI 보정본**이다(분포 밖이면 물리값과 같다).
        "pet": round(_pet_ai, 1),
        "feels_like_c": round(_pet_ai, 1),
        "parallel_ms": _par_ms,        # 동시 실행 구간(기하·날씨·위성) 소요 — 튜닝 근거

        "ai_applied": _ai_on, "ai_confidence": round(_ai_conf, 2),
        "mrt_c": round(float(r.mrt.tmrt), 1),
        # WBGT(暑さ指数) — 일본의 공용 지표. 환경성 값은 관측점(광역)이라 그늘/볕 구분이 없고,
        # 우리는 이 좌표의 MRT(건물 그늘·노면 포함)에서 산출하므로 같은 거리에서도 보도별로 다르다.
        **_wbgt_fields(obs.temperature_c, obs.humidity_pct, r.pedestrian_wind_ms, float(r.mrt.tmrt)),
        # 노면온도 — 강아지 발바닥 화상(肉球やけど)·유모차 높이 판단의 근거
        "ground_c": round(float(r.mrt.ground_temp_c), 1),
        "paw_risk": _paw_risk(float(r.mrt.ground_temp_c)),
        "wall_c": (round(sum(wall_temp.values()) / len(wall_temp), 1)
                   if isinstance(wall_temp, dict)
                   else (round(wall_temp, 1) if wall_temp is not None else None)),
        "wall_dirs": ({k: round(v, 1) for k, v in wall_temp.items()}
                      if isinstance(wall_temp, dict) else None),
        "wall_material": wall_mat["material"],
        "wall_albedo": wall_mat["albedo"], "wall_mix": wall_mat["mix"],
        "street_width_m": _sw.get("width_m"), "hw_ratio": _sw.get("hw_ratio"),
        "street_axis_deg": _sw.get("axis_deg"), "snapped_m": _sw.get("snapped_m"),
        "svf": round(svf, 3), "n_buildings": svf_r.get("n_buildings"),
        "svf_source": svf_r.get("source"),
        # 수관(위성 나무 높이)이 이 지점에서 실제로 잡혔는지 (2026-09-16).
        # 없으면 화면에서 "나무 차폐 반영 안 됨"과 "나무가 없음"을 구분할 수 없다.
        # 일본 타일 적재를 확인하려고 컨테이너에 들어가야 했던 것도 이 필드가 없어서였다.
        "n_canopy": svf_r.get("n_canopy"),
        "canopy_observed": svf_r.get("canopy_observed"),
        "gvi": round(gvi, 3), "gvi_src": gvi_src,
        # 위성 표면 지수 원값 — 논문·대시보드에서 '인공피복률' 로 바로 쓸 수 있게 노출 (2026-09-13).
        # 계산은 전부터 하고 있었는데 재질 분율로만 쪼개져 밖으로 안 나갔다.
        "surface": (None if surface is None else {
            "ndvi": round(float(surface.get("ndvi") or 0.0), 3),
            "ndwi": round(float(surface.get("ndwi") or 0.0), 3),
            "albedo": round(float(surface.get("albedo") or 0.0), 3),
            "veg_frac": surface.get("veg_frac"),      # 식생 분율
            "imp_frac": surface.get("imp_frac"),      # 인공피복 비율
            "water": surface.get("water"),
        }),
        "material_src": mat_src,
        "materials": [{"m": m, "f": round(f, 2)} for m, f in
                      ((mm.material, mm.fraction) for mm in mats)],
        "exposure": exposure, "shade_note": shade_note,
        "tree_shade": tree_f,        # 0~1, 태양 방향 나무의 차광 비율
        "weather": {"ta": round(obs.temperature_c, 1),
                    "rh": round(obs.humidity_pct, 0),
                    "wind_ms": round(obs.wind_speed_ms, 1),
                    "src": "Open-Meteo"},
        "solar": {"elev": round(sol.solar_elevation_deg, 1),
                  "az": round(sol.solar_azimuth_deg, 1),
                  # 일사를 노출한다 (2026-09-17). 엔진은 ghi/dni/dhi 를 이미 계산해
                  # MRT 의 sw_direct(직달x차폐)·sw_diffuse(산란xSVF)·sw_reflected(지면반사)에
                  # 넣고 있는데, 응답에 없어서 **밖에서 검증할 수가 없었다.**
                  #   · 9/16 현장에서 개방 아스팔트 두 곳의 엔진 오차가 -10.8 / +8.7 로
                  #     방향이 반대였다. 일사가 문제인지 SVF 가 문제인지 응답만으로는 못 갈랐다.
                  #   · clearsky_ratio = ghi / ghi_clearsky. 1 에 가까우면 맑음, 0.4 면 구름이
                  #     해를 가린 것이다. "그때 해가 구름에 있었나"를 사진으로 추측하지 않아도 된다.
                  "ghi": round(float(getattr(sol, "ghi", 0.0) or 0.0), 1),
                  "dni": round(float(getattr(sol, "dni", 0.0) or 0.0), 1),
                  "dhi": round(float(getattr(sol, "dhi", 0.0) or 0.0), 1),
                  "ghi_clearsky": round(float(getattr(sol, "ghi_clearsky", 0.0) or 0.0), 1),
                  "clearsky_ratio": (
                      round(float(sol.ghi) / float(sol.ghi_clearsky), 2)
                      if getattr(sol, "ghi_clearsky", 0) else None),
                  # MRT 를 이루는 네 갈래 — 어느 항이 모자라거나 넘치는지 바로 보인다
                  "sw_direct": round(float(getattr(r.mrt, "sw_direct", 0.0) or 0.0), 1),
                  "sw_diffuse": round(float(getattr(r.mrt, "sw_diffuse", 0.0) or 0.0), 1),
                  "sw_reflected": round(float(getattr(r.mrt, "sw_reflected", 0.0) or 0.0), 1),
                  "lw_surface": round(float(getattr(r.mrt, "lw_surface", 0.0) or 0.0), 1),
                  "fp": round(float(getattr(r.mrt, "fp", 0.0) or 0.0), 3)},
        "note": "GSV 미사용",
    }


@router.get("/archive/geo_vpti", include_in_schema=False)
async def archive_geo_vpti(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    x_field_key: str | None = Header(None),
) -> dict:
    """진단 — GSV 없이 전세계 체감(VPTI) 산출. X-Field-Key 게이트."""
    _require_field_key(x_field_key)
    try:
        return await _geo_vpti_compute(lat, lon)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}", "lat": lat, "lon": lon}


@router.get("/vpti/geo/at", include_in_schema=False)
async def vpti_geo_at(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
) -> dict:
    """앱용 공개 — 좌표 → 지금 이 순간 체감기후(VPTI). GSV 없이 전세계.

    기하 SVF(사전적재 건물이 있는 지역) + 기하 그늘 + Open-Meteo 실시간 날씨 +
    Sentinel-2 위성 GVI + 교정엔진. '이동 중 체감' 경험의 서버 엔진.
    건물 미적재 지역은 404(reason). 몸씨(한국·GSV) 경로와 별개의 새 앱용.
    """
    try:
        out = await _geo_vpti_compute(lat, lon)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"geo vpti error: {e}",
        ) from e
    if not out.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=out.get("reason", "해당 좌표 데이터 없음"),
        )
    return out


@router.get("/jp/wbgt", include_in_schema=False)
async def jp_wbgt(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    age: int | None = Query(None, ge=0, le=120),
    conditions: str | None = Query(None, description="취약군 콤마 구분: cardio,resp,diabetes,kidney,pregnant"),
) -> dict:
    """일본판 — 이 자리의 공식 暑さ指数(WBGT)와 등급 (2026-09-18).

    일본인은 「暑さ指数 33」으로 말한다. PET·VPTI 는 모른다. 그래서 일본판은 이 숫자로 말한다.

    **무엇을 돌려주나**
      · `official` : 가장 가까운 환경성 지점의 **예측**(그대로 전재). 지점명·거리를 함께 준다.
      · `level`    : 환경성·일본생기상학회 지침 등급. 나이·질환을 주면 경계를 낮춰 더 일찍 경고한다.
      · `alert`    : 환경성 발표 경보(있으면). **우리가 판정하지 않는다.**

    ⚠️ 기상업무법: 자체 예보를 일반에 공표하려면 기상청 허가가 필요하다(제17조).
       그래서 미래값은 환경성 것을 **그대로** 전하고, 우리 엔진 값은 `/vpti/geo/at`(실황)으로 낸다.
    ⚠️ 이용 조건상 출처를 반드시 표기한다 — 응답의 `attribution` 을 화면에 띄울 것.
    """
    from app.services import wbgt_jp as W
    cond = [c.strip() for c in (conditions or "").split(",") if c.strip()] or None

    pool = None
    try:
        from app.services.skyline import _get_pool
        pool = await _get_pool()
    except Exception:  # noqa: BLE001
        pool = None
    if pool is None:
        return {"ok": False, "reason": "DB 없음", "attribution": W.ATTRIBUTION}

    async with pool.acquire() as c:
        # 가장 가까운 지점 — 거리는 대략(도 단위 제곱)으로 고른 뒤 m 로 환산해 알려준다.
        row = await c.fetchrow(
            "SELECT p.point_id, p.name, p.lat, p.lon, f.wbgt, f.target_at, f.issued_at "
            "FROM wbgt_point p JOIN wbgt_forecast f ON f.point_id = p.point_id "
            "WHERE f.target_at >= NOW() - INTERVAL '90 minutes' "
            "ORDER BY (p.lat-$1)^2 + (p.lon-$2)^2, f.target_at LIMIT 1", lat, lon)
        alert = await c.fetchrow(
            "SELECT area, level, issued_at FROM wbgt_alert "
            "WHERE target_date >= CURRENT_DATE ORDER BY target_date LIMIT 1")

    if row is None:
        return {"ok": False, "reason": "가까운 지점의 예측이 없다(시즌 밖이거나 미적재)",
                "attribution": W.ATTRIBUTION}

    import math as _m
    _dy = (float(row["lat"]) - lat) * 111320.0
    _dx = (float(row["lon"]) - lon) * 111320.0 * _m.cos(_m.radians(lat))
    return {
        "ok": True,
        "official": {
            "point_id": row["point_id"], "name": row["name"],
            "wbgt": round(float(row["wbgt"]), 1),
            "target_at": row["target_at"].isoformat(),
            "issued_at": row["issued_at"].isoformat() if row["issued_at"] else None,
            "distance_m": round(_m.hypot(_dx, _dy)),
        },
        "level": W.level_of(float(row["wbgt"]), age, cond),
        "alert": ({"area": alert["area"], "level": alert["level"],
                   "issued_at": alert["issued_at"].isoformat() if alert["issued_at"] else None}
                  if alert else None),
        "attribution": W.ATTRIBUTION,
    }


@router.post("/archive/backfill_sv_status", include_in_schema=False)
async def archive_backfill_sv_status(
    request: Request,
    gsv: int = 0,
    limit: int = 200,
    x_field_key: str | None = Header(None),
):
    """기존 기록의 pano_dist_m·sv_status 백필 (X-Field-Key 필요, 2026-09-09).

    - Tier1(항상): 분석 실패(SVF≈0) → sv_status='failed'. 정확·GSV 불필요.
    - Tier2(gsv=1): 미정 격자를 _resolve_pano_id 로 재조회해 거리→ok/substituted.
      ⚠️ 재조회는 **현재** 파노라마 기준(과거 분석 당시와 다를 수 있음) → 근사.
      파노 원본(ID·좌표·날짜)은 저장하지 않고 파생 거리·상태만 UPDATE.
      한 번에 limit 격자만 처리 → remaining>0 이면 반복 호출.
    """
    _require_field_key(x_field_key)
    import asyncio as _asyncio
    from app.services.street_view import StreetViewNotFound
    arch = getattr(request.app.state, "archive", None)
    orch = getattr(request.app.state, "orchestrator", None)
    if arch is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="archive 미초기화")
    failed_n = await arch.backfill_sv_failed()
    grids_done = rows_updated = 0
    remaining = 0
    if gsv and orch is not None:
        grids = await arch.grids_needing_sv(limit=limit)
        for g in grids:
            la, lo = g["lat"], g["lon"]
            try:
                _pid, _pl, _po, dist = await orch._resolve_pano_id(la, lo)
                if dist is None:
                    continue
                st = "ok" if dist <= 50 else "substituted"
                rows_updated += await arch.apply_sv_grid(la, lo, int(dist), st)
            except StreetViewNotFound:
                rows_updated += await arch.apply_sv_grid(la, lo, None, "failed")
            except Exception:  # noqa: BLE001
                continue
            grids_done += 1
            await _asyncio.sleep(0.05)
        rem = await arch.grids_needing_sv(limit=1)
        remaining = 1 if rem else 0
    return {"failed_marked": failed_n, "grids_processed": grids_done,
            "rows_updated": rows_updated,
            "more_remaining": bool(remaining),
            "note": "Tier2 거리는 현재 파노라마 기준 근사(과거≠현재)" if gsv else
                    "Tier1만 실행(gsv=1 로 거리 백필)"}


@router.get("/archive/export_training", include_in_schema=False)
async def archive_export_training(
    request: Request,
    x_field_key: str | None = Header(None),
):
    """학습층 CSV 내보내기 — 실외 측정 전체(인수인계 260909 명세). X-Field-Key 필요.
    ⚠️ imagery_src='gsv' 유래 svf/gvi/bvi는 약관상 ML 학습 불가(출처 컬럼으로 노출)."""
    _require_field_key(x_field_key)
    from fastapi.responses import Response
    import csv, io
    arch = getattr(request.app.state, "archive", None)
    if arch is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="archive 미초기화")
    rows = await arch.export_training()
    cols = ["observed_at", "lat", "lon", "svf", "gvi", "bvi", "air_temp",
            "humidity", "wind_ms", "pvpti", "mrt", "risk_level", "cloud",
            "cloud_src", "imagery_src", "indoor", "source",
            "pano_dist_m", "sv_status"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return Response(
        content=buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=momssi_measurements_export_260909.csv"})


@router.get("/archive/training_counts", include_in_schema=False)
async def archive_training_counts(
    request: Request,
    x_field_key: str | None = Header(None),
) -> dict:
    """학습층 요약(전체·격자·기간·출처별). X-Field-Key 필요."""
    _require_field_key(x_field_key)
    arch = getattr(request.app.state, "archive", None)
    if arch is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="archive 미초기화")
    return await arch.training_counts()


@router.get("/archive/mapillary_probe", include_in_schema=False)
async def archive_mapillary_probe(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    x_field_key: str | None = Header(None),
) -> dict:
    """진단 — 한 좌표에서 Mapillary vs GSV 로 SVF/GVI/BVI 비교 (켜기 전 품질 확인용).

    라이브 경로·캐시와 무관. Mapillary 커버리지·재투영 품질을 GSV와 나란히 본다.
    """
    _require_field_key(x_field_key)
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        return {"ok": False, "reason": "orchestrator 미초기화"}
    try:
        r = await orch.probe_sources(lat, lon)
        r["ok"] = True
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


@router.get("/archive/wind_compare", include_in_schema=False)
async def archive_wind_compare(
    request: Request,
    lat: float = Query(...), lon: float = Query(...),
    wind_ms: float = Query(3.0, ge=0.0, le=40.0),
    x_field_key: str | None = Header(None),
) -> dict:
    """보행자 풍속 병기 — 지금 앱 방식(app.core.pwi, 고정지수 0.30) vs
    물리 방식(Macdonald 거칠기 + 로그분포 + Cionco 캐노피 감쇠).

    건물 형태(높이·건폐율 λp·정면밀도 λf)는 건축물대장(building_risk)에서 자동조회,
    λf 결측 시 거리영상 BVI 로 대리. 실측 풍속계 교정 전까지 물리는 '병기' 참고용.
    """
    _require_field_key(x_field_key)
    orch = getattr(request.app.state, "orchestrator", None)
    sp = await orch.spatial_at(lat, lon) if orch is not None else None
    if not sp:
        return {"ok": False, "reason": "이 지점 SVF/BVI 없음(거리뷰 커버리지 밖)"}
    svf, bvi, gvi = sp["svf"], sp["bvi"], sp.get("gvi", 0.0)

    # ── 지금 앱 방식 ──
    from app.core.pwi import compute_pwi as _pwi_now, WindCondition as _WC
    now = _pwi_now(_WC(speed_ms=wind_ms, direction_deg=270.0, temperature_c=28.0),
                   svf=svf, bvi=bvi)

    # ── 건물 형태 자동조회 ──
    height_m = lam_p = lam_f = None
    bname = None
    lf_src = "건축물대장(기하)"
    try:
        from app.services import building as _b
        br = await _b.building_risk(lat, lon)
        if br is not None:
            bname = br.building_name
            height_m = br.height_m
            lam_p = br.cov_ratio
            lam_f = br.frontal_ratio
    except Exception:  # noqa: BLE001
        pass
    if lam_f is None:               # 건축물대장 기하 결측 → BVI 대리
        lam_f = min(max(bvi, 0.02), 0.9); lf_src = "BVI 대리"
    if lam_p is None:
        lam_p = min(max(1.0 - svf, 0.05), 0.9)  # 개방도 역수 근사

    # ── 물리 방식 ──
    from vpti_core.pwi_physics import pedestrian_wind_physics
    phy = pedestrian_wind_physics(wind_ms, lambda_p=lam_p, lambda_f=lam_f, height_m=height_m)

    u_now = now.pedestrian_wind_speed_ms
    u_phy = phy.pedestrian_wind_speed_ms
    return {
        "ok": True, "lat": lat, "lon": lon, "wind_ref_10m_ms": wind_ms,
        "building_name": bname,
        "spatial": {"svf": round(svf, 3), "bvi": round(bvi, 3), "gvi": round(gvi, 3)},
        "morphology": {"height_m": phy.height_m, "lambda_p": phy.lambda_p,
                       "lambda_f": phy.lambda_f, "lambda_f_src": lf_src},
        "now": {"u_p_ms": round(u_now, 3), "pct_of_ref": round(u_now / wind_ms * 100, 1) if wind_ms else None,
                "profile_exponent": 0.30, "urban_reduction": round(now.urban_reduction, 3)},
        "physics": {"u_p_ms": round(u_phy, 3), "pct_of_ref": round(u_phy / wind_ms * 100, 1) if wind_ms else None,
                    "z0_urban_m": phy.z0_urban_m, "disp_height_m": phy.disp_height_m,
                    "profile_exponent_equiv": phy.profile_exponent_equiv,
                    "u_canopy_top_ms": phy.u_canopy_top_ms, "canopy_atten_a": phy.canopy_atten_a},
        "note": ("지금 방식=고정 프로파일지수 0.30(밀도는 SVF·BVI로 약하게). "
                 "물리 방식=건물밀도→거칠기로 프로파일 자체를 밀도에 묶음. "
                 "절대 m/s는 참고, 밀도 상대패턴 신뢰 — 풍속계 실측으로 교정 예정."),
    }


@router.get("/cache/stats", summary="캐시 상태 (관리자용)")
async def cache_stats(request: Request) -> dict:
    """현재 캐시된 panoId 수 등 모니터링 정보."""
    cache = getattr(request.app.state, "cache", None)
    if cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cache not initialized",
        )

    return {
        "pano_cached": await cache.count_pano_cache(),
        "redis_ok": await cache.ping(),
    }
