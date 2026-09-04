"""
설정 관리.

.env 파일에서 환경변수를 읽어 타입 안전하게 제공합니다.
pydantic-settings가 자동 검증·변환합니다.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 전역 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: Literal["development", "production", "test"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://climax:climax@localhost:5432/climax"
    database_echo: bool = False

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl_pano: int = 0  # 0 = 영구
    redis_cache_ttl_weather: int = 600  # 10분

    # External APIs
    google_streetview_api_key: str = ""
    google_streetview_signing_secret: str = ""
    ncp_maps_client_id: str = ""
    ncp_maps_client_secret: str = ""
    # 카카오 로컬(장소·주소·역지오코딩) REST API 키 — https://developers.kakao.com
    # 목적지 "상호" 검색의 1순위. 비어 있으면 기존 NCP 주소검색 + OSM 폴백으로만 동작(하위호환).
    kakao_rest_api_key: str = ""
    kma_api_key: str = ""
    kma_base_url: str = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
    # 기상청 API허브(apihub.kma.go.kr) 회원 인증키 — ASOS 실시간(일사·전운량·지면온도).
    # 비어 있으면 ASOS 미사용(기존 SKY 예보 경로만) — 하위호환.
    kma_apihub_key: str = ""
    # 건물 열취약 판정 (실내축 2단계) — V-World 리버스지오코딩 + 건축HUB 건축물대장
    vworld_api_key: str = ""
    building_api_key: str = ""

    # 거리영상(Street View) 월 호출 상한 — **이미지 요청 수** 기준.
    # 파노라마 1지점 = 5-view = 5요청. 구글 무료 한도가 SKU당 월 1만이라 9,000에서 멈춘다.
    # ① 초과 과금 차단 ② 약관 3.2.3(a)(ii) bulk download 로 읽힐 트래픽 차단.
    # 0 이하 = 상한 없음(개발용). 운영에서는 반드시 양수로 둘 것.
    streetview_monthly_image_budget: int = 9000

    # 측정 이력 적재(핫스팟 자산). 일반 사용자 대상 수집은 동의 화면·방침 개정 후 켤 것.
    archive_enabled: bool = True

    # 현장 실측 대조 API 접근키 (2026-08-16) — 대표 전용 검증 도구용.
    # 비어 있으면 /field/check 엔드포인트가 404 (일반 사용자에겐 존재 자체가 안 보임).
    # .env.prod 에 FIELD_KEY=<긴 임의 문자열> 로 설정.
    field_key: str = ""

    # Gemini 이미지 생성 (개선 시뮬 개념도, 2026-09-05). 비어 있으면 개념도 기능만 꺼진다.
    gemini_api_key: str = ""
    gemini_image_model: str = "gemini-2.5-flash-image"

    # NCP Object Storage
    ncp_object_access_key: str = ""
    ncp_object_secret_key: str = ""
    ncp_object_bucket: str = "climax-mvp"
    ncp_object_region: str = "kr-standard"

    # ML Model
    segformer_model_name: str = "nvidia/segformer-b0-finetuned-ade-512-512"
    segformer_checkpoint_path: str = ""
    segformer_device: Literal["auto", "cpu", "cuda"] = "auto"

    # VSI weights (논문 기본값)
    vsi_weight_svf: float = Field(default=0.5, ge=0.0, le=1.0)
    vsi_weight_gvi: float = Field(default=0.3, ge=0.0, le=1.0)
    vsi_weight_bvi: float = Field(default=0.2, ge=0.0, le=1.0)

    # CORS
    # 쾌적경로 데모 HTML은 앱 번들 안에서 file:// 로 뜬다 → Origin 헤더가 "null" 이다.
    # 장소검색·역지오코딩은 사용자 데이터가 없는 공개 읽기 API라 null 허용이 안전하다.
    cors_origins: str = (
        "http://localhost:3000,https://climaxapp.kr,https://www.climaxapp.kr,null"
    )

    @field_validator("cors_origins")
    @classmethod
    def parse_cors_origins(cls, v: str) -> str:
        """쉼표 구분 문자열을 그대로 유지 (미들웨어에서 split)."""
        return v.strip()

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS origins를 리스트로 반환."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def vsi_weights(self) -> tuple[float, float, float]:
        """VSI 선형 결합 가중치 (SVF, GVI, BVI)."""
        return (self.vsi_weight_svf, self.vsi_weight_gvi, self.vsi_weight_bvi)


@lru_cache
def get_settings() -> Settings:
    """설정 싱글톤. 앱 수명 동안 재사용."""
    return Settings()
