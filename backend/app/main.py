"""
ClimaX Backend FastAPI 앱.

실행:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

OpenAPI 문서:
    http://localhost:8000/docs   (Swagger)
    http://localhost:8000/redoc  (ReDoc)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.routes import router as api_router
from app.api.websocket import router as ws_router
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 수명주기 훅.

    시작 시:
    - Redis 연결
    - Street View / KMA 클라이언트 생성
    - SegFormer 로드 (시간 걸림, ~20초)
    - Orchestrator 조립

    종료 시:
    - 모든 비동기 클라이언트 graceful close
    """
    settings = get_settings()
    logger.info(
        "ClimaX backend starting | env={} | vsi_weights={}",
        settings.app_env,
        settings.vsi_weights,
    )

    # 지연 import로 의존성 로딩 시간 단축 (설정 오류 조기 감지)
    from app.ml.segformer import get_segformer_service
    from app.services.cache import CacheService
    from app.services.kma import KMAClient
    from app.services.orchestrator import VPTIOrchestrator
    from app.services.street_view import GoogleStreetViewClient

    # 캐시
    cache = CacheService(redis_url=settings.redis_url)
    redis_ok = await cache.ping()
    logger.info("Redis connection: {}", "OK" if redis_ok else "FAIL")

    # 외부 API 클라이언트
    sv_client: GoogleStreetViewClient | None = None
    kma_client: KMAClient | None = None
    orchestrator: VPTIOrchestrator | None = None

    if settings.google_streetview_api_key and settings.kma_api_key:
        sv_client = GoogleStreetViewClient(
            api_key=settings.google_streetview_api_key,
            signing_secret=settings.google_streetview_signing_secret,
        )
        kma_client = KMAClient(api_key=settings.kma_api_key)

        # SegFormer (시간 걸림)
        segformer = get_segformer_service()
        try:
            segformer.load()
        except Exception as e:
            logger.error("SegFormer load failed: {}. /vpti/at will be unavailable.", e)
            segformer = None  # type: ignore[assignment]

        if segformer is not None and segformer.is_loaded():
            orchestrator = VPTIOrchestrator(
                cache=cache,
                street_view=sv_client,
                kma=kma_client,
                segformer=segformer,
            )
            logger.info("Orchestrator ready")
        else:
            logger.warning(
                "Orchestrator disabled (SegFormer not loaded). "
                "Core endpoints still work; /vpti/at and /track won't."
            )
    else:
        logger.warning(
            "External API keys missing (.env). Orchestrator disabled. "
            "Only pure-computation endpoints available."
        )

    # 앱 state에 주입
    app.state.cache = cache
    app.state.orchestrator = orchestrator

    yield

    logger.info("ClimaX backend shutting down")
    await cache.close()
    if sv_client is not None:
        await sv_client.close()
    if kma_client is not None:
        await kma_client.close()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ClimaX VPTI Core Engine",
        description=(
            "무센서 기반 체감기후 인텔리전스 플랫폼 — "
            "VSI + SMTI + PWI 통합 실시간 산출 API"
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": "ClimaX VPTI Core Engine",
            "version": "0.1.0",
            "docs": "/docs",
        }

    return app


app = create_app()
