"""
실내 체감기후 (실내 pVPTI) — v2 (2026-08-10).

야외 pVPTI와 같은 사상: "기온"이 아니라 "이 조건에서 사람이 느끼는 더위"를 낸다.
실내는 하늘·바람 대신 **건물(축열·일사 취득)** 이 환경을 지배하므로:

  ① 실내 기온 추정 (비냉방 가정):
     T_in = T_mean(오늘 평균) + D×(T_out − T_mean) + 일사취득 + 야간축열
       - D(감쇠계수): 구조별 열질량 — 콘크리트 0.5 / 조적 0.65 / 경량 0.8
         (무거운 건물일수록 바깥 기온 변화가 실내에 덜/늦게 전달됨)
       - 일사취득: 태양고도×(1−구름)×건물취약(score) — 낮에 외피가 데워져 실내로
       - 야간축열: 해가 지면 낮에 머금은 열 방출 — 취약할수록 큼
  ② 실내 체감 = T_in + 습도 기여(후덥지근함, 야외 엔진과 동일 공식) + 무풍 보정
  ③ 위험 등급: 야외와 동일 등급표(_classify_risk) + 취약군 앞당김
  ④ 이어러블 실측(ambient)이 있으면 T_in 추정을 실측으로 대체 —
     "예상"이 아니라 이 사람이 실제 겪는 실내 체감이 된다.

정확도 로드맵: v2(본 파일, 물리 근사) → 실측·추정 짝 데이터로 건물별 보정 학습
→ BTLI(면·존별 외부 열부하) 결합 정밀화.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from loguru import logger

from app.core.vpti import WeatherContext, _classify_risk, _humidity_contribution
from app.services.kma import KST


@dataclass
class IndoorResult:
    indoor_pvpti: float          # 실내 체감기후 (°C)
    indoor_risk: str             # safe … severe (취약군 반영)
    t_in_est: float              # 실내 기온 (추정 또는 실측)
    measured: bool               # True = 이어러블 실측 사용
    basis: dict                  # 계산 근거 (투명성 — 화면 "왜?" 표시용)


def _damping(structure: str | None) -> float:
    """구조별 감쇠계수 — 열질량 클수록 바깥 변화가 실내에 덜 전달."""
    s = structure or ""
    if any(k in s for k in ("철근", "철골", "콘크리트")):
        return 0.5
    if any(k in s for k in ("벽돌", "블록", "조적", "시멘트", "석")):
        return 0.65
    if any(k in s for k in ("목", "판넬", "패널", "샌드위치", "경량", "컨테이너")):
        return 0.8
    return 0.6


def _solar_factor(now: datetime) -> float:
    """태양고도 근사(0~1) — 6시 일출~19시 일몰 사인 곡선 (여름 한반도 근사)."""
    h = now.hour + now.minute / 60.0
    if h <= 6.0 or h >= 19.0:
        return 0.0
    return math.sin(math.pi * (h - 6.0) / 13.0)


def vulnerability_level(age: int | None, conditions: list[str] | None) -> int:
    """앱 BodyRiskEngine과 동일 규칙 (심혈관+2 / 호흡·당뇨·신장+1 / 임신+1 / 고령·영유아)."""
    lvl = 0
    c = set(conditions or [])
    if "cardio" in c:
        lvl += 2
    if c & {"resp", "diabetes", "kidney"}:
        lvl += 1
    if "pregnant" in c:
        lvl += 1
    if age is not None:
        if age >= 75 or age <= 4:
            lvl += 2
        elif age >= 65:
            lvl += 1
    return min(lvl, 3)


def _floor_delta(
    floor: int | None, total_floors: int | None, sf: float, cloud: float
) -> float:
    """층 위치 보정 — 같은 건물도 층에 따라 실내 열환경이 다르다.

    · 최상층: 지붕이 직접 일사를 받음 — 낮엔 크게(+최대 1.6), 밤에도 축열 방출(+0.5)
    · 중상층(상위 20%): 외피 노출 비중 큼 (+0.3)
    · 중간층: 위아래 세대가 단열 완충 역할 (−0.3)
    · 저층(1~2층): 지붕 영향 없음, 지면 그늘 (0)
    """
    if floor is None or total_floors is None or total_floors <= 1:
        return 0.0
    if floor >= total_floors:                      # 최상층
        day = sf * (1.0 - cloud) * 1.6             # 지붕 일사
        night = 0.5 if sf == 0.0 else 0.0          # 야간 지붕 축열 방출
        return day + night
    if floor / total_floors >= 0.8:
        return 0.3
    if floor <= 2:
        return 0.0
    return -0.3


def compute_indoor(
    *,
    t_out_now: float,
    t_mean_today: float,
    humidity_pct: float,
    cloud_fraction: float,        # 0(맑음)~1(흐림)
    building_score: int,          # 건축물대장 취약점수 0~7
    structure: str | None,
    age: int | None = None,
    conditions: list[str] | None = None,
    ambient_measured: float | None = None,   # 이어러블 실측 (있으면 추정 대체)
    floor: int | None = None,                # 거주 층 (예: 22)
    total_floors: int | None = None,         # 건물 전체 층수 (예: 25)
    now: datetime | None = None,
) -> IndoorResult:
    now = now or datetime.now(KST)
    sf = _solar_factor(now)
    d = _damping(structure)
    fd = _floor_delta(floor, total_floors, sf, cloud_fraction)

    if ambient_measured is not None:
        t_in = ambient_measured                    # 실측이 왕 — 층 보정 불필요
        measured = True
    else:
        # ① 축열 감쇠: 실내는 오늘 평균기온 주변에서 바깥 변화를 D만큼만 따라감
        t_in = t_mean_today + d * (t_out_now - t_mean_today)
        # ② 일사 취득: 해가 떠 있고 하늘이 열려 있으면 외피가 데워져 실내로 (+0 ~ +2.6)
        t_in += sf * (1.0 - cloud_fraction) * (0.8 + 0.25 * building_score)
        # ③ 야간 축열 방출: 해가 진 뒤 낮에 머금은 열 (+1.0 ~ +2.75)
        if sf == 0.0:
            t_in += 1.0 + 0.25 * building_score
        # ④ 층 위치 보정 (최상층 지붕 일사 / 중간층 완충)
        t_in += fd
        measured = False

    # ④ 실내 체감 = 기온 + 습도(후덥지근함, 야외 엔진과 동일 공식) + 무풍 보정
    wx = WeatherContext(
        temperature_c=t_in, humidity_pct=humidity_pct,
        wind_speed_ms=0.1, wind_direction_deg=0.0,
    )
    season = wx.season
    dh = _humidity_contribution(t_in, humidity_pct, season)
    still_air = 0.5 if season == "summer" else 0.0   # 실내 무풍 — 여름 체감 가중
    feel = t_in + dh + still_air

    # ⑤ 위험 등급 (야외와 동일 등급표) + 취약군 앞당김 (레벨당 1.0°C, 상한 3.0)
    shift = min(vulnerability_level(age, conditions) * 1.0, 3.0)
    risk = _classify_risk(feel + shift, season)

    return IndoorResult(
        indoor_pvpti=round(feel, 1),
        indoor_risk=risk,
        t_in_est=round(t_in, 1),
        measured=measured,
        basis={
            "t_out_now": round(t_out_now, 1),
            "t_mean_today": round(t_mean_today, 1),
            "damping": d,
            "solar_factor": round(sf, 2),
            "cloud_fraction": round(cloud_fraction, 2),
            "building_score": building_score,
            "humidity_delta": round(dh, 1),
            "vulnerability_shift": shift,
            "floor": floor,
            "floor_delta": round(fd, 1),
        },
    )
