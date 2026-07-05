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

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.config import get_settings
from app.core.smti import MaterialFraction
from app.core.vpti import WeatherContext, compute_vpti
from app.core.vsi import (
    DEFAULT_WEIGHTS,
    ViewSegmentation,
    compute_vsi,
    compute_vsi_from_components,
)
from app.schemas.vpti import (
    HealthResponse,
    VPTIRequest,
    VPTIResponse,
    VSIComponentsIn,
    VSIResultOut,
    ViewSegmentationIn,
)
from app.services.street_view import StreetViewNotFound

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
    lat: float = Query(..., ge=-90.0, le=90.0, description="위도 [deg]"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="경도 [deg]"),
) -> VPTIResponse:
    """위경도만 주면 Street View, 기상청, SegFormer 모두 자동 호출.

    캐시 hit 시 <100ms, miss 시 1~3초 소요.
    """
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized. Backend may still be starting.",
        )

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

    response = VPTIResponse(**result.as_dict(), precipitation=precipitation)
    return response


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
