"""
PWI (Pedestrian Wind Index) — 보행자 풍환경 보정계수.

특허: 발명내용설명서_정숙진_260527
      (공공 기상 풍속의 보행자 공간 환경 기반 변환 시스템 및 방법)

공공 기상관측망(AWS/ASOS)의 기준 풍속 u_ref 를, 거리영상에서 추출한 시각
환경 인자(SVF·BVI·GVI)와 풍향-도로축 관계로 보정하여 보행자 높이 풍속 u_p 를
산출한다. CFD·GIS 없이 보정계수만으로 변환한다.

【수학식 1】 u_p = PWI · u_ref                         (PWI > 0 ⇒ u_p ≥ 0)
【수학식 2】 Δθ = ‖ 풍향 − 도로축 ‖ 을 [0°, 90°]로 정규화 (도로축 양방향성)
【수학식 3】 w_i = exp(κ·cos(φ_i − θ_wind)) / Σ_j exp(κ·cos(φ_j − θ_wind))
              (수평 4-view 풍향 가중, von Mises 형태)
【수학식 4】 PWI_rule = (C_h · C_z · C_ch)^(1/3)
              (수평 차폐 · 천정 개방 · channeling 의 기하평균)
【수학식 5】 c = exp(β·(x − x_ref))   (모든 부분 보정계수, 항상 양수)

하이브리드 구조 (특허 '다'항):
  최종 PWI = PWI_rule · R_AI
  · R_AI : 2차 AI 잔차 보정계수 (룰베이스가 못 잡는 비선형 효과)
  · Fallback: AI 신뢰도 < 임계치 또는 학습 분포 밖이면 R_AI = 1
              → AI 없이도 룰베이스만으로 항상 동작.

물리적 일관성: 부분 보정계수가 모두 지수함수(수학식 5)라 양수 → 기하평균(수학식 4)
도 양수 → R_AI > 0 → PWI > 0 → u_p ≥ 0 (음수 풍속 불가능).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import DEFAULT_CONFIG, PWIConfig

# 수평 4-view 인덱스: 전방 F, 우측 R, 후방 B, 좌측 L (특허 '라'항)
HorizontalLabel = str


@dataclass(frozen=True, slots=True)
class HorizontalView:
    """수평 4-view 중 한 방향의 입력.

    azimuth_deg 는 절대 방위각(0=북, 90=동, 기상학 convention)이며,
    보통 보행자 진행 방향(heading)에 F/R/B/L = +0/+90/+180/+270 을 더해 만든다.
    """

    label: HorizontalLabel       # "F" | "R" | "B" | "L"
    azimuth_deg: float           # φ_i : 절대 방위각 [0, 360)
    bvi: float                   # 해당 view의 건축 가시율 [0, 1]
    gvi: float                   # 해당 view의 식생 가시율 [0, 1]

    def __post_init__(self) -> None:
        if not 0.0 <= self.azimuth_deg < 360.0:
            raise ValueError(f"azimuth_deg={self.azimuth_deg} out of [0, 360)")
        for name, value in (("bvi", self.bvi), ("gvi", self.gvi)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name}={value} out of [0, 1] (view={self.label})")


@dataclass(frozen=True, slots=True)
class PWIResult:
    pedestrian_wind_speed_ms: float  # u_p (수학식 1)
    pwi: float                       # 최종 보정계수 PWI
    pwi_rule: float                  # 1차 룰베이스 보정계수 (수학식 4)
    ai_residual: float               # R_AI (Fallback 시 1.0)
    used_fallback: bool              # AI 미사용/저신뢰 → Fallback 여부
    delta_theta_deg: float           # Δθ (수학식 2)
    c_horizontal: float              # C_h : 수평 차폐
    c_zenith: float                  # C_z : 천정 개방
    c_channel: float                 # C_ch : channeling
    view_weights: dict[str, float]   # w_i (수학식 3)

    def as_dict(self) -> dict:
        return {
            "pedestrian_wind_speed_ms": round(self.pedestrian_wind_speed_ms, 3),
            "pwi": round(self.pwi, 4),
            "pwi_rule": round(self.pwi_rule, 4),
            "ai_residual": round(self.ai_residual, 4),
            "used_fallback": self.used_fallback,
            "delta_theta_deg": round(self.delta_theta_deg, 2),
            "c_horizontal": round(self.c_horizontal, 4),
            "c_zenith": round(self.c_zenith, 4),
            "c_channel": round(self.c_channel, 4),
            "view_weights": {k: round(v, 4) for k, v in self.view_weights.items()},
        }


def road_axis_angle_diff(wind_direction_deg: float, road_axis_deg: float) -> float:
    """【수학식 2】 풍향과 도로축의 각도 차이 Δθ 를 [0°, 90°]로 정규화.

    도로축은 양방향성(주기 180°)을 가지므로:
        d  = |θ_wind − θ_road| mod 180        ∈ [0, 180)
        Δθ = 90 − |d − 90|                    ∈ [0, 90]

    Δθ ≈ 0  : 풍향이 도로축과 평행 → channeling(풍속 증가)
    Δθ ≈ 90 : 풍향이 도로축과 수직 → 건물 차폐(풍속 감소)
    """
    d = abs(wind_direction_deg - road_axis_deg) % 180.0
    return 90.0 - abs(d - 90.0)


def von_mises_weights(
    views: list[HorizontalView],
    wind_direction_deg: float,
    kappa: float,
) -> dict[str, float]:
    """【수학식 3】 수평 4-view 의 풍향 기반 가중치 (von Mises 형태).

        w_i = exp(κ·cos(φ_i − θ_wind)) / Σ_j exp(κ·cos(φ_j − θ_wind))

    κ ↑ 일수록 풍향과 일치하는 view에 가중치 집중. Σ w_i = 1.
    """
    if kappa < 0.0:
        raise ValueError(f"kappa={kappa} must be ≥ 0")

    logits = {
        v.label: kappa * math.cos(math.radians(v.azimuth_deg - wind_direction_deg))
        for v in views
    }
    # softmax (수치 안정화를 위해 최댓값 차감)
    m = max(logits.values())
    exps = {k: math.exp(val - m) for k, val in logits.items()}
    z = sum(exps.values())
    return {k: val / z for k, val in exps.items()}


def _partial_coefficient(beta: float, x: float, x_ref: float) -> float:
    """【수학식 5】 부분 보정계수 c = exp(β·(x − x_ref)). 항상 양수."""
    return math.exp(beta * (x - x_ref))


def _horizontal_shelter(
    views: list[HorizontalView],
    weights: dict[str, float],
    config: PWIConfig,
) -> float:
    """C_h : 수평 차폐 보정계수.

    각 view의 차폐 부분 보정계수(건물 BVI·수목 GVI 기반, 수학식 5)를 풍향
    가중치 w_i 로 통합. 부분 보정계수가 지수함수이므로 로그공간 가중합
    = 가중 기하평균으로 결합(특허: "풍향 기반 가중치로 통합").

        c_i = exp(−β_bvi·(BVI_i − ref) − β_gvi·(GVI_i − ref))   ( <1: 차폐)
        C_h = Π_i c_i^{w_i} = exp(Σ_i w_i · log c_i)

    부호: 건물·수목이 많을수록(BVI·GVI↑) 차폐로 바람 감소 → 음의 민감도.
    """
    log_ch = 0.0
    for v in views:
        # exp(...) 의 지수부를 직접 누적 (log c_i)
        log_ci = (
            -config.beta_bvi * (v.bvi - config.ref_bvi)
            - config.beta_gvi * (v.gvi - config.ref_gvi)
        )
        log_ch += weights[v.label] * log_ci
    return math.exp(log_ch)


def _zenith_open(svf: float, config: PWIConfig) -> float:
    """C_z : 천정 개방 보정계수 (수학식 5).

    천정(상향) view는 수평 방위가 정의되지 않아 별도 처리(특허 '라'항).
    하늘이 많이 보일수록(SVF↑) 바람 유입↑ → 양의 민감도 → C_z > 1.
    """
    return _partial_coefficient(config.beta_svf, svf, config.ref_svf)


def _channeling(delta_theta_deg: float, config: PWIConfig) -> float:
    """C_ch : channeling 보정계수 (수학식 2 → 5).

        정렬도 a = cos(2·Δθ)   ( Δθ=0°→+1 평행, Δθ=90°→−1 수직 )
        C_ch = exp(β_channel · a)

    풍향이 도로축과 평행(Δθ≈0)하면 a≈+1 → C_ch>1 (channeling 증폭),
    수직(Δθ≈90)이면 a≈−1 → C_ch<1 (차폐 감쇠).
    """
    alignment = math.cos(2.0 * math.radians(delta_theta_deg))
    return _partial_coefficient(config.beta_channel, alignment, 0.0)


def compute_pwi(
    wind_speed_ms: float,
    wind_direction_deg: float,
    road_axis_deg: float,
    svf: float,
    horizontal_views: list[HorizontalView],
    ai_residual: float | None = None,
    ai_confidence: float | None = None,
    config: PWIConfig = DEFAULT_CONFIG.pwi,
) -> PWIResult:
    """PWI 산출 — 수학식 1~5 + 룰베이스/AI 하이브리드/Fallback.

    Args:
        wind_speed_ms: 기준 풍속 u_ref [m/s] (AWS/ASOS, 음수 불가).
        wind_direction_deg: 풍향 θ_wind [deg], 0=북.
        road_axis_deg: 도로축 방향 θ_road [deg] (거리영상에서 추출).
        svf: 천정 개방도 SVF [0, 1] (VSI에서 받음).
        horizontal_views: 수평 4-view (F/R/B/L) 입력.
        ai_residual: 2차 AI 잔차 보정계수 R_AI. None이면 Fallback(=1.0).
        ai_confidence: AI 신뢰도 [0, 1]. 임계치 미만이면 Fallback.
        config: PWI 계수 설정.

    Returns:
        PWIResult — u_p, PWI 및 모든 중간 보정계수 분해.
    """
    if wind_speed_ms < 0.0:
        raise ValueError(f"wind_speed_ms={wind_speed_ms} must be ≥ 0")
    if not 0.0 <= svf <= 1.0:
        raise ValueError(f"svf={svf} out of [0, 1]")
    if len(horizontal_views) != 4:
        raise ValueError(f"수평 4-view가 필요함, got {len(horizontal_views)}개")

    # --- 1차 룰베이스 보정 ---
    delta_theta = road_axis_angle_diff(wind_direction_deg, road_axis_deg)  # 수학식 2
    weights = von_mises_weights(horizontal_views, wind_direction_deg, config.von_mises_kappa)  # 수학식 3

    c_h = _horizontal_shelter(horizontal_views, weights, config)
    c_z = _zenith_open(svf, config)
    c_ch = _channeling(delta_theta, config)

    pwi_rule = (c_h * c_z * c_ch) ** (1.0 / 3.0)  # 수학식 4 (기하평균)

    # --- 2차 AI 잔차 보정 + Fallback (특허 '다'항) ---
    use_fallback = (
        ai_residual is None
        or ai_residual <= 0.0
        or (ai_confidence is not None and ai_confidence < config.ai_confidence_threshold)
    )
    r_ai = 1.0 if use_fallback else float(ai_residual)

    # --- 최종 PWI 및 안전 클램프 ---
    pwi = pwi_rule * r_ai
    pwi = min(max(pwi, config.pwi_min), config.pwi_max)

    u_p = pwi * wind_speed_ms  # 수학식 1

    return PWIResult(
        pedestrian_wind_speed_ms=u_p,
        pwi=pwi,
        pwi_rule=pwi_rule,
        ai_residual=r_ai,
        used_fallback=use_fallback,
        delta_theta_deg=delta_theta,
        c_horizontal=c_h,
        c_zenith=c_z,
        c_channel=c_ch,
        view_weights=weights,
    )


def build_horizontal_views(
    heading_deg: float,
    bvi_by_label: dict[str, float],
    gvi_by_label: dict[str, float],
) -> list[HorizontalView]:
    """보행자 진행 방향(heading)과 view별 BVI·GVI로 수평 4-view 구성.

    F/R/B/L 의 절대 방위각 = heading + {0, 90, 180, 270} (mod 360).
    """
    offsets = {"F": 0.0, "R": 90.0, "B": 180.0, "L": 270.0}
    views: list[HorizontalView] = []
    for label, off in offsets.items():
        views.append(
            HorizontalView(
                label=label,
                azimuth_deg=(heading_deg + off) % 360.0,
                bvi=bvi_by_label[label],
                gvi=gvi_by_label[label],
            )
        )
    return views
