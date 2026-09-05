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
    "/dog/course",
    summary="개 기준 산책 코스 추천 — 출발점으로 되돌아오는 순환 코스 3개",
)
async def dog_course(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    minutes: int = Query(30, ge=10, le=120, description="목표 산책 시간(분)"),
    air_c: float = Query(..., description="기온 °C"),
    ghi: float = Query(0.0, ge=0.0, le=1400.0, description="유효 일사 W/m² (축열 반영값이면 더 좋음)"),
    wind_ms: float = Query(1.0, ge=0.0, le=40.0),
    rh: float = Query(60.0, ge=0.0, le=100.0),
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

    cond = dc.Conditions(air_c=air_c, ghi=ghi, wind_ms=wind_ms, rh=rh,
                         rain=rain, withers_cm=withers_cm, vuln_offset_c=vuln_offset_c)
    graph = dc.build_graph(roads.get("elements", []), cond)
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
    return FileResponse(p, media_type="text/html")


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

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="엔진 미기동")

    result, telemetry = await orchestrator.compute_personalized(
        lat, lon, bio=Biometrics(), archive_consent=False,
    )
    base = result.comfort  # ComfortResult (PET/UTCI 입력 echo 포함)
    est = {
        "pvpti": round(result.pvpti, 2),
        "risk": result.risk_level,
        "index": base.index,
        "mrt": round(base.tr, 2),          # 엔진 Tmrt
        "ta": round(base.tdb, 2),          # 엔진이 쓴 기온(기상청)
        "rh": round(base.rh, 1),
        "u_p": round(base.v_input, 2),     # 보행자 풍속 (클램프 전)
        "weather_source": telemetry.weather_source,
    }

    # 잔차 — 실측이 있는 항목만
    resid = {}
    for k_meas, k_est in (("ta", "ta"), ("rh", "rh"), ("wind_ms", "u_p"),
                          ("pet", "pvpti"), ("mrt", "mrt")):
        if meas.get(k_meas) is not None and est.get(k_est) is not None:
            resid[k_est] = round(est[k_est] - float(meas[k_meas]), 2)

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
) -> dict:
    """좌표의 건물 정보 + **실내 체감기후(실내 pVPTI)** 를 반환.

    야외 pVPTI와 같은 사상 — 건물 축열·시간대별 일사·구름으로 실내 기온을 추정하고,
    습도(후덥지근함)·무풍을 반영한 '실내 체감'과 취약군 반영 위험 등급을 낸다.
    이어러블 실측(ambient)이 오면 추정 대신 실측 기반 — '이 사람이 실제 겪는 실내 체감'.
    데이터: V-World + 건축물대장 + 기상청 — 공개 데이터만, 좌표·생체 미저장.
    """
    from app.services.building import building_risk, to_dict
    from app.services.indoor import compute_indoor

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
            )
            if len(_INDOOR_MEMORY) > 10_000:      # 폭주 방지 — 오래된 것부터 비움
                _INDOOR_MEMORY.clear()
            _INDOOR_MEMORY[mem_key] = (_time.time(), ind.t_in_est)
            result["est_indoor_c"] = ind.t_in_est
            result["indoor_pvpti"] = ind.indoor_pvpti
            result["indoor_risk"] = ind.indoor_risk
            result["indoor_measured"] = ind.measured
            result["indoor_basis"] = ind.basis
        except Exception as e:  # noqa: BLE001
            logger.warning(f"indoor pvpti failed: {e}")
    return result


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
    return r


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
