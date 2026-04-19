"""
VPTI (Visual Physical Thermal Index) 통합 엔진.

사업계획서 정의:
    "VPTI : 공간·재질·풍환경·기상요소를 통합하여 개인 체감기후를 정량화한 지표"

핵심 수식 (초안 — 실증 후 조정):
    VPTI = base_pet + Δ_VSI(공간) + Δ_SMTI(재질·일사) + Δ_PWI(바람)

여기서 base_pet은 PET(Physiologically Equivalent Temperature) 또는
체감온도의 기본값 (기상청 기상요소로 산출), Δ는 공간 특성에 의한 편차.

전략:
1. 여름/겨울 모드 분기 — 같은 바람이 여름엔 쾌적, 겨울엔 추위
2. 위험도 카테고리 — B2G 실증 리포트용 5단계 분류
3. 원인 분해 — 어떤 요소가 체감을 악화시켰는지 설명 가능 (사업계획서 핵심)
4. 행동 가이드 매핑 — 지수 → 추천 행동 텍스트
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np

from app.core.pwi import PWIResult, WindCondition, compute_pwi
from app.core.smti import (
    MaterialFraction,
    SMTIResult,
    SolarCondition,
    compute_smti,
    compute_solar_position,
    estimate_shading_from_svf,
)
from app.core.vsi import VSIResult, ViewSegmentation, compute_vsi

Season = Literal["summer", "winter", "transition"]
RiskLevel = Literal["safe", "caution", "warning", "danger", "severe"]


@dataclass(frozen=True, slots=True)
class WeatherContext:
    """기상청에서 받은 현재 기상 조건."""

    temperature_c: float  # 기온 [°C]
    humidity_pct: float  # 상대습도 [%]
    wind_speed_ms: float  # 풍속 [m/s] @ 10m
    wind_direction_deg: float  # 풍향 [deg]
    precipitation_mm: float = 0.0  # 강수량 [mm/h]

    @property
    def season(self) -> Season:
        """기온 기준 간이 계절 판정.

        실제 구현은 위도+날짜 기반으로 정교하게 하되, 체감 로직에선 기온이 더 중요.
        """
        if self.temperature_c >= 23.0:
            return "summer"
        if self.temperature_c <= 10.0:
            return "winter"
        return "transition"


@dataclass(frozen=True, slots=True)
class VPTIResult:
    """VPTI 최종 산출 결과 — 사용자 UI에 필요한 모든 정보 포함."""

    vpti: float  # 통합 체감기후 값 (체감 기온 근사, °C 단위)
    risk_level: RiskLevel
    season: Season

    # 구성 지수 (재사용·캐싱용)
    vsi: VSIResult
    smti: SMTIResult
    pwi: PWIResult

    # 원인 분해 (UI의 "위험 원인 분석" 화면용)
    contribution_space: float  # VSI 기여 Δ°C
    contribution_material: float  # SMTI 기여 Δ°C
    contribution_wind: float  # PWI 기여 Δ°C (여름 음수, 겨울 양수)

    # 행동 가이드
    action_guide: str

    timestamp: str

    def as_dict(self) -> dict:
        return {
            "vpti": round(self.vpti, 2),
            "risk_level": self.risk_level,
            "season": self.season,
            "vsi": self.vsi.as_dict(),
            "smti": self.smti.as_dict(),
            "pwi": self.pwi.as_dict(),
            "contributions": {
                "space": round(self.contribution_space, 2),
                "material": round(self.contribution_material, 2),
                "wind": round(self.contribution_wind, 2),
            },
            "action_guide": self.action_guide,
            "timestamp": self.timestamp,
        }


# ===== 핵심 매핑 상수 (실증 후 튜닝 예정) =====

# VSI 기여 스케일: 최고 VSI → 여름 +5°C, 겨울 +2°C (열섬 효과)
VSI_SUMMER_MAX_DELTA = 5.0
VSI_WINTER_MAX_DELTA = 2.0

# SMTI 기여 스케일: 뜨거운 표면 → 여름 +4°C
SMTI_SUMMER_MAX_DELTA = 4.0
SMTI_WINTER_MAX_DELTA = 1.5

# PWI 기여 스케일: 바람은 계절별 부호 다름
# 여름: 바람 ↑ → 체감 ↓ (최대 -3°C)
# 겨울: 바람 ↑ → 체감 ↓ (한파 체감, 최대 -8°C)
# 이상기온(환절기): 영향 작음
PWI_SUMMER_MAX_DELTA = -3.0  # 쾌적
PWI_WINTER_MAX_DELTA = -8.0  # 위험


def _vsi_contribution(vsi_val: float, season: Season) -> float:
    """VSI로부터 체감 Δ°C 기여 계산.

    높은 VSI = 복사열 노출 ↑. 여름엔 크게, 겨울엔 작게 기여.
    """
    if season == "summer":
        scale = VSI_SUMMER_MAX_DELTA
    elif season == "winter":
        scale = VSI_WINTER_MAX_DELTA
    else:
        scale = (VSI_SUMMER_MAX_DELTA + VSI_WINTER_MAX_DELTA) / 2

    # VSI는 이론상 [0, 1] (논문 가중치 기준 0.3~1.0). 정규화.
    return scale * float(np.clip(vsi_val, 0.0, 1.0))


def _smti_contribution(smti_val: float, season: Season) -> float:
    """SMTI로부터 체감 Δ°C 기여 계산."""
    if season == "summer":
        scale = SMTI_SUMMER_MAX_DELTA
    elif season == "winter":
        scale = SMTI_WINTER_MAX_DELTA
    else:
        scale = (SMTI_SUMMER_MAX_DELTA + SMTI_WINTER_MAX_DELTA) / 2

    return scale * float(np.clip(smti_val, 0.0, 1.0))


def _pwi_contribution(
    pwi_val: float, season: Season, temperature_c: float
) -> float:
    """PWI로부터 체감 Δ°C 기여 계산.

    계절별 부호/스케일 다름.
    """
    if season == "summer":
        scale = PWI_SUMMER_MAX_DELTA  # 음수 (쾌적)
    elif season == "winter":
        scale = PWI_WINTER_MAX_DELTA  # 음수 (체감 한파)
    else:
        # 환절기는 기온 의존
        if temperature_c >= 18.0:
            scale = PWI_SUMMER_MAX_DELTA / 2
        else:
            scale = PWI_WINTER_MAX_DELTA / 2

    return scale * float(np.clip(pwi_val, 0.0, 1.0))


def _classify_risk(vpti: float, season: Season) -> RiskLevel:
    """계절별 체감기후 위험도 분류.

    임계값은 기상청·질병관리청 온열·한랭질환 가이드 참고.
    """
    if season == "summer":
        if vpti >= 38.0:
            return "severe"
        if vpti >= 35.0:
            return "danger"
        if vpti >= 32.0:
            return "warning"
        if vpti >= 28.0:
            return "caution"
        return "safe"
    if season == "winter":
        if vpti <= -18.0:
            return "severe"
        if vpti <= -12.0:
            return "danger"
        if vpti <= -5.0:
            return "warning"
        if vpti <= 3.0:
            return "caution"
        return "safe"
    # transition
    if vpti >= 32.0 or vpti <= -5.0:
        return "warning"
    if vpti >= 28.0 or vpti <= 3.0:
        return "caution"
    return "safe"


def _generate_action_guide(
    risk: RiskLevel,
    season: Season,
    contribution_space: float,
    contribution_material: float,
    contribution_wind: float,
) -> str:
    """위험도·원인 기반 행동 가이드 생성.

    사업계획서의 "② 위험 원인 분석 → 행동 가이드" 화면용.
    가장 큰 기여 요인을 특정하고 대응 행동을 제시합니다.
    """
    if risk == "safe":
        return "현재 체감기후는 안전한 수준입니다. 야외활동에 큰 무리가 없습니다."

    contributions = {
        "공간 구조": abs(contribution_space),
        "표면 재질": abs(contribution_material),
        "바람": abs(contribution_wind),
    }
    dominant = max(contributions, key=contributions.get)

    if season == "summer":
        base_msgs = {
            "caution": "충분한 수분 섭취와 가벼운 햇빛 가림을 권장합니다.",
            "warning": "그늘로 이동하고 10분 내 휴식을 취하세요.",
            "danger": "즉시 실내 또는 쿨링 쉼터로 대피하세요.",
            "severe": "야외활동 중단. 가까운 실내로 긴급 대피하세요.",
        }
        cause_msgs = {
            "공간 구조": "하늘이 열려 복사열이 강합니다.",
            "표면 재질": "발 밑 표면이 축적한 열이 체감을 크게 높이고 있습니다.",
            "바람": "바람이 약해 열이 빠지지 않고 있습니다.",
        }
        return f"{cause_msgs[dominant]} {base_msgs[risk]}"

    if season == "winter":
        base_msgs = {
            "caution": "바람을 막는 외투와 장갑을 착용하세요.",
            "warning": "노출 부위를 덮고 짧은 야외활동만 하세요.",
            "danger": "동상 위험. 가까운 실내로 이동하세요.",
            "severe": "한랭질환 심각 위험. 즉시 실내 대피가 필요합니다.",
        }
        cause_msgs = {
            "공간 구조": "공간이 개방되어 열이 빠르게 소실됩니다.",
            "표면 재질": "차가운 지표면이 체감 온도를 낮추고 있습니다.",
            "바람": "바람이 강해 체감 한파 위험이 높습니다.",
        }
        return f"{cause_msgs[dominant]} {base_msgs[risk]}"

    return "환절기 체감기후가 변동성이 큽니다. 복장 조절에 유의하세요."


def compute_vpti(
    views_5: list[ViewSegmentation],
    materials: list[MaterialFraction],
    weather: WeatherContext,
    latitude: float,
    longitude: float,
    timestamp: datetime | None = None,
    vsi_weights: tuple[float, float, float] = (0.5, 0.3, 0.2),
) -> VPTIResult:
    """VPTI 통합 파이프라인.

    전체 흐름:
    1. VSI 계산 (5-view 세그멘테이션 → 공간 지수)
    2. 태양 위치 계산 (위경도 + 시각)
    3. SVF → 음영 계수 추정
    4. SMTI 계산 (재질 × 열물성 × 태양 × 음영)
    5. PWI 계산 (기상 풍속 × 공간 downscaling)
    6. 기상 기반 base 체감온도 + Δ 합산 → VPTI
    7. 위험도 분류 + 행동 가이드

    Args:
        views_5: 5-view 세그멘테이션 결과 (VSI용).
        materials: 재질 점유 비율 리스트 (SMTI용).
        weather: 기상청 관측값.
        latitude, longitude: 위경도 (태양 위치 계산용).
        timestamp: 시각. None이면 현재시각.
        vsi_weights: VSI 선형 결합 가중치.

    Returns:
        VPTIResult — 모든 중간 결과와 최종 값, 행동 가이드.
    """
    dt = timestamp or datetime.now()

    # 1. VSI
    vsi_result = compute_vsi(views_5, weights=vsi_weights)

    # 2. 태양 위치
    solar = compute_solar_position(latitude, longitude, dt)

    # 3. 음영 계수
    shading = estimate_shading_from_svf(vsi_result.svf)

    # 4. SMTI
    smti_result = compute_smti(
        materials=materials,
        solar=solar,
        shading_coefficient=shading,
        timestamp=dt,
    )

    # 5. PWI
    wind_cond = WindCondition(
        speed_ms=weather.wind_speed_ms,
        direction_deg=weather.wind_direction_deg,
        temperature_c=weather.temperature_c,
    )
    pwi_result = compute_pwi(
        wind=wind_cond, svf=vsi_result.svf, bvi=vsi_result.bvi
    )

    # 6. VPTI 통합
    season = weather.season
    delta_space = _vsi_contribution(vsi_result.vsi, season)
    delta_material = _smti_contribution(smti_result.smti, season)
    delta_wind = _pwi_contribution(
        pwi_result.pwi, season, weather.temperature_c
    )

    # Base: 기상청 기온을 단순히 그대로 출발값으로 사용
    # (향후 PET 공식으로 정교화 가능)
    base_temp = weather.temperature_c

    vpti_value = base_temp + delta_space + delta_material + delta_wind

    # 7. 위험도 + 가이드
    risk = _classify_risk(vpti_value, season)
    guide = _generate_action_guide(
        risk, season, delta_space, delta_material, delta_wind
    )

    return VPTIResult(
        vpti=vpti_value,
        risk_level=risk,
        season=season,
        vsi=vsi_result,
        smti=smti_result,
        pwi=pwi_result,
        contribution_space=delta_space,
        contribution_material=delta_material,
        contribution_wind=delta_wind,
        action_guide=guide,
        timestamp=dt.isoformat(),
    )
