"""
API 입출력 Pydantic 스키마.

core.* 엔진 내부 dataclass와는 별개로, HTTP 경계에서 사용하는
스키마를 따로 정의합니다. 이유:
- JSON 직렬화·역직렬화 자동
- OpenAPI 스키마 자동 생성 (FastAPI /docs)
- 유효성 검증 (위경도 범위 등)
- 입력과 출력 구조가 달라야 하는 경우 유연성
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ===== 공통 =====

class LatLon(BaseModel):
    """위경도 좌표."""

    lat: float = Field(..., ge=-90.0, le=90.0, description="위도 [deg]")
    lon: float = Field(..., ge=-180.0, le=180.0, description="경도 [deg]")


# ===== VSI =====

class ViewSegmentationIn(BaseModel):
    """세그멘테이션 결과 (외부에서 이미 계산된 경우)."""

    direction: Literal["front", "back", "left", "right", "up"]
    sky_ratio: float = Field(..., ge=0.0, le=1.0)
    vegetation_ratio: float = Field(..., ge=0.0, le=1.0)
    building_ratio: float = Field(..., ge=0.0, le=1.0)
    ground_ratio: float = Field(0.0, ge=0.0, le=1.0)


class VSIComponentsIn(BaseModel):
    """이미 산출된 SVF/GVI/BVI로 VSI만 재계산 (테스트·비교용)."""

    svf: float = Field(..., ge=0.0, le=1.0)
    gvi: float = Field(..., ge=0.0, le=1.0)
    bvi: float = Field(..., ge=0.0, le=1.0)
    weights: tuple[float, float, float] | None = Field(
        None,
        description="VSI 선형 결합 가중치. None이면 서버 기본값 (0.5, 0.3, 0.2) 사용.",
    )


class VSIResultOut(BaseModel):
    """VSI 산출 결과."""

    svf: float
    gvi: float
    bvi: float
    vsi: float
    weights: tuple[float, float, float]
    category: Literal["Low", "Moderate", "High"]


# ===== SMTI =====

class MaterialFractionIn(BaseModel):
    material: Literal[
        "asphalt",
        "concrete",
        "vegetation",
        "glass",
        "metal",
        "soil",
        "water",
        "brick",
        "stone",
        "wood",
        "unknown",
    ]
    fraction: float = Field(..., ge=0.0, le=1.0)


class SMTIResultOut(BaseModel):
    smti: float
    material_contributions: dict[str, float]
    solar_intensity: float
    shading_coefficient: float
    timestamp: str


# ===== PWI =====

class PWIResultOut(BaseModel):
    pedestrian_wind_speed_ms: float
    pwi: float
    profile_reduction: float
    urban_reduction: float
    wind_chill_severity: Literal["calm", "mild", "strong", "hazardous"]


# ===== VPTI =====

class WeatherIn(BaseModel):
    """기상 입력 (테스트·수동 입력용). 운영 시엔 기상청 API로 자동 조회."""

    temperature_c: float = Field(..., ge=-50.0, le=60.0)
    humidity_pct: float = Field(..., ge=0.0, le=100.0)
    wind_speed_ms: float = Field(..., ge=0.0, le=100.0)
    wind_direction_deg: float = Field(..., ge=0.0, lt=360.0)
    precipitation_mm: float = Field(0.0, ge=0.0)


class VPTIRequest(BaseModel):
    """VPTI 산출 요청."""

    location: LatLon
    views: list[ViewSegmentationIn] = Field(
        ..., min_length=5, max_length=5, description="5-view 세그멘테이션"
    )
    materials: list[MaterialFractionIn] = Field(
        ..., min_length=1, description="재질 점유 비율"
    )
    weather: WeatherIn | None = Field(
        None,
        description="None이면 기상청 API 자동 조회 (운영). 테스트 시 직접 전달.",
    )
    timestamp: datetime | None = None
    vsi_weights: tuple[float, float, float] | None = None


class VPTIContributions(BaseModel):
    space: float
    material: float
    wind: float
    humidity: float = 0.0  # 습도(후덥지근함) 기여 Δ°C


class PrecipitationHour(BaseModel):
    time: str
    in_hours: int
    pty: str | None = None      # 강수형태 (없음/비/비눈/눈/소나기…)
    sky: str | None = None      # 하늘상태 (맑음/구름많음/흐림)
    precip_mm: float = 0.0


class PrecipitationOut(BaseModel):
    """강수 컨텍스트 — VPTI와 분리된 외부 예보 레이어(확률/현재상태)."""

    raining_now: bool = False
    current_precip_mm: float = 0.0
    rain_expected_6h: bool = False
    onset_in_hours: int | None = None
    max_precip_mm: float = 0.0
    umbrella_recommended: bool = False
    advice: str = ""
    hourly: list[PrecipitationHour] = Field(default_factory=list)


class VPTIResponse(BaseModel):
    """VPTI 최종 응답."""

    model_config = ConfigDict(from_attributes=True)

    vpti: float
    risk_level: Literal["safe", "caution", "warning", "danger", "severe"]
    season: Literal["summer", "winter", "transition"]
    vsi: VSIResultOut
    smti: SMTIResultOut
    pwi: PWIResultOut
    contributions: VPTIContributions
    action_guide: str
    timestamp: str
    # 강수는 VPTI 숫자에 섞지 않고 별도 컨텍스트로만 노출
    precipitation: PrecipitationOut | None = None


# ===== Health =====

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    timestamp: str
