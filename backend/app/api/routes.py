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

from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
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

    return PersonalizedVPTIResponse(
        **result.as_dict(),
        weather_source=telemetry.weather_source,
        lookahead=lookahead,
    )


@router.get("/geocode", summary="주소/장소 → 좌표 (NCP 주소 + OSM 장소명)")
async def geocode(
    request: Request,
    query: str = Query(..., min_length=1, description="검색할 주소 또는 장소명"),
) -> JSONResponse:
    """목적지 문자열 → 좌표.

    1순위 NCP Geocoding(주소 정밀), 없으면 2순위 Nominatim(장소명: 부산대학교 등).
    """
    from app.services.ncp_directions import nominatim_geocode

    directions = getattr(request.app.state, "directions", None)
    result = None
    source = None

    # 1) NCP 주소 검색 (있을 때)
    if directions is not None:
        try:
            result = await directions.geocode(query)
            if result is not None:
                source = "ncp"
        except Exception as e:  # noqa: BLE001
            logger.warning("NCP geocode 실패(무시): {}", e)

    # 2) 장소명 폴백 (OSM Nominatim)
    if result is None:
        result = await nominatim_geocode(query)
        if result is not None:
            source = "osm"

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="위치를 찾을 수 없습니다. 장소명이나 주소를 조금 더 구체적으로 입력해 보세요.",
        )
    lat, lon, label = result
    return JSONResponse({"lat": lat, "lon": lon, "address": label, "source": source})


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

    points: list[dict] = []
    for (lat, lon) in samples:
        try:
            result, _ = await orchestrator.compute(lat=lat, lon=lon)
        except StreetViewNotFound:
            continue  # 거리뷰 없는 지점은 건너뜀
        except Exception as e:  # noqa: BLE001
            logger.warning("경로 지점 계산 실패({},{}): {}", lat, lon, e)
            continue
        d = result.as_dict()
        points.append({
            "lat": round(lat, 6), "lon": round(lon, 6),
            "vpti": d["vpti"], "risk_level": d["risk_level"],
            "contributions": d["contributions"], "action_guide": d["action_guide"],
        })

    if not points:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="경로 상에서 계산 가능한 지점이 없습니다(거리뷰 부재 등).",
        )

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

    vs = [p["vpti"] for p in points]
    profile = {
        "meta": {
            "origin": {"lat": olat, "lon": olon, "name": "현재 위치"},
            "dest": {"lat": dlat, "lon": dlon, "name": "목적지"},
            "n_points": len(points), "sample": False,
            "note": "실시간 경로 — NCP 길찾기 + 지점별 VPTI",
            "weather": weather_meta,
            "precipitation": precipitation,
        },
        "summary": {
            "vpti_min": round(min(vs), 2), "vpti_max": round(max(vs), 2),
            "vpti_avg": round(sum(vs) / len(vs), 2),
        },
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
    }


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
    ambient: float | None = Query(None, description="이어러블 실측 실내온도(°C) — 있으면 추정 대체"),
    floor: int | None = Query(None, ge=1, le=120, description="거주 층 — 최상층/중간층 보정"),
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
    if b is None:
        raise HTTPException(status_code=404, detail="건물 정보를 찾을 수 없음")
    result = to_dict(b)
    result["est_indoor_c"] = None
    result["indoor_pvpti"] = None
    result["indoor_risk"] = None
    result["indoor_measured"] = False

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None:
        try:
            obs = await orchestrator.kma.get_current_observation(lat, lon)
            # 오늘 평균기온(축열 기준) — 단기예보의 오늘 시간대 평균, 실패 시 현재기온
            t_mean = obs.temperature_c
            try:
                fcst = await orchestrator.kma.get_short_term_forecast(lat, lon)
                today = [
                    f.temperature_c for f in fcst
                    if f.forecast_for.date() == datetime.now(KST).date()
                    and f.temperature_c is not None
                ]
                if today:
                    t_mean = sum(today) / len(today)
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

            ind = compute_indoor(
                t_out_now=obs.temperature_c,
                t_mean_today=t_mean,
                humidity_pct=obs.humidity_pct,
                cloud_fraction=cloud,
                building_score=b.score,
                structure=b.structure,
                age=age,
                conditions=conditions.split(",") if conditions else None,
                ambient_measured=ambient,
                floor=floor,
                total_floors=b.floors,
            )
            result["est_indoor_c"] = ind.t_in_est
            result["indoor_pvpti"] = ind.indoor_pvpti
            result["indoor_risk"] = ind.indoor_risk
            result["indoor_measured"] = ind.measured
            result["indoor_basis"] = ind.basis
        except Exception as e:  # noqa: BLE001
            logger.warning(f"indoor pvpti failed: {e}")
    return result


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
