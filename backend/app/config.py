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
    kma_api_key: str = ""
    kma_base_url: str = "https://apihub.kma.go.kr/api/typ02"

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
    cors_origins: str = "http://localhost:3000"

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
