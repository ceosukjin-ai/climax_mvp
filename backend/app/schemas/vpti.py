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
    weather_source: str = "실측"   # 실측 | 캐시 | 추정 (KMA 타임아웃 폴백 구분)
    # 강수는 VPTI 숫자에 섞지 않고 별도 컨텍스트로만 노출
    precipitation: PrecipitationOut | None = None


# ===== PHI (생리 개인화, pVPTI) =====

class BiometricsIn(BaseModel):
    """애플워치 스냅샷. 전부 Optional(부분 결측 허용). 서버 미저장(계산 후 폐기)."""

    hr: float | None = Field(None, ge=20.0, le=250.0, description="심박 [bpm]")
    activity: float | None = Field(
        None, ge=0.0, le=50.0, description="활동에너지 소비율 [kcal/min]"
    )
    hr_rest: float | None = Field(None, ge=20.0, le=150.0, description="휴식심박 [bpm]")
    hr_max: float | None = Field(None, ge=100.0, le=250.0, description="최대심박 [bpm]")


class ProfileDerivedIn(BaseModel):
    """개인화 파생값 — 민감정보 최소화(나이·성별·체격만, 기저질환 등 미전송)."""

    age: int | None = Field(None, ge=0, le=120)
    sex: Literal["male", "female"] | None = None
    height_cm: float | None = Field(None, ge=50.0, le=250.0)
    weight_kg: float | None = Field(None, ge=10.0, le=300.0)
    observed_hr_max: float | None = Field(None, ge=100.0, le=250.0)


class PersonalizedVPTIRequest(BaseModel):
    """pVPTI 산출 요청(수동 입력 — B1). 좌표·시각으로 일사/MRT 계산."""

    location: LatLon
    views: list[ViewSegmentationIn] = Field(..., min_length=5, max_length=5)
    materials: list[MaterialFractionIn] = Field(..., min_length=1)
    weather: WeatherIn
    road_axis_deg: float = Field(0.0, ge=0.0, lt=360.0, description="도로축 방향 [deg]")
    timestamp: datetime | None = Field(None, description="평가 시각(None이면 현재)")
    sky_code: int | None = Field(None, description="KMA SKY 코드(1/3/4)")
    biometrics: BiometricsIn
    profile: ProfileDerivedIn | None = None


class AutoPersonalizedVPTIRequest(BaseModel):
    """자동 pVPTI 요청(B2) — 좌표+생체신호만. scene/weather 는 서버가 자동 산출."""

    location: LatLon
    timestamp: datetime | None = Field(None, description="평가 시각(None이면 현재)")
    biometrics: BiometricsIn
    profile: ProfileDerivedIn | None = None
    # prefetch(앞 미리 분석)용 — 모두 선택적. heading 없으면 세션 직전좌표로 방위 계산.
    heading: float | None = Field(
        None, ge=0.0, lt=360.0, description="진행 방향[deg] 0~359 (prefetch용)"
    )
    speed_kmh: float | None = Field(
        None, ge=0.0, le=300.0, description="이동 속도[km/h] (prefetch 거리 제한용)"
    )
    session_id: str | None = Field(
        None, max_length=128, description="세션 식별(heading 없을 때 직전좌표 방위 계산)"
    )


class LookaheadItem(BaseModel):
    """진행 방향 앞 지점의 미리 산출된 체감기후(prefetch 캐시 기반)."""

    distance_m: float
    pvpti: float
    risk_level: Literal["safe", "caution", "warning", "danger", "severe"]


class PersonalizedVPTIResponse(BaseModel):
    """pVPTI 응답. base_* 는 개인화 전 참조값(둘 다 PET)."""

    pvpti: float
    base_vpti: float
    delta_personalization: float
    risk_level: Literal["safe", "caution", "warning", "danger", "severe"]
    base_risk_level: Literal["safe", "caution", "warning", "danger", "severe"]
    strain_index: float
    observed_hrr: float | None
    expected_hrr: float | None
    metabolic_met: float | None
    hr_max_used: float | None
    season: Literal["summer", "winter", "transition"]
    stress_category: str
    comfort: dict
    weather_source: str = "실측"   # 실측 | 캐시 | 추정 (KMA 타임아웃 폴백 구분)
    # 진행 방향 앞 지점(prefetch로 이미 분석된 것만). 없으면 빈 배열.
    lookahead: list[LookaheadItem] = Field(default_factory=list)


# ===== Health =====

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    timestamp: str
