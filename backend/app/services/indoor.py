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

from app.core.vpti import (
    WeatherContext,
    _classify_risk,
    _humidity_contribution,
    _saturation_vapor_pressure,
)
from app.services.kma import KST


def _indoor_relative_humidity(rh_out_pct: float, t_out_c: float, t_in_c: float) -> float:
    """외기 상대습도 → 실내 상대습도 변환 (2026-08-14 수정).

    창문을 닫은 방은 **수증기압(절대습도)이 외기와 거의 같다.** 온도만 높아지므로
    포화수증기압이 커져 **상대습도는 낮아진다.** 외기 RH를 실내 온도에 그대로
    쓰면 습도 기여가 과대평가된다.

    예) 외기 21.8°C·89% → 수증기압 23.2 hPa. 실내 25.0°C에서는 RH 73.6%이지
        89%가 아니다. 이 차이가 체감으로 약 +2.6°C 과대를 만들었다.

    검증: 8/12 BT-3 실측(실내 29.1°C·RH 52%)에서 외기 RH를 그대로 썼다면
          습도항이 1.6°C가 아니라 6.5°C가 됐을 것.
    """
    e_out = (rh_out_pct / 100.0) * _saturation_vapor_pressure(t_out_c)
    rh_in = e_out / _saturation_vapor_pressure(t_in_c) * 100.0
    return min(100.0, max(0.0, rh_in))


@dataclass
class IndoorResult:
    indoor_pvpti: float          # 실내 체감기후 (°C)
    indoor_risk: str             # safe … severe (취약군 반영)
    t_in_est: float              # 실내 기온 (추정 또는 실측)
    measured: bool               # True = 이어러블 실측 사용
    basis: dict                  # 계산 근거 (투명성 — 화면 "왜?" 표시용)
    # 행동 권고 (2026-08-11) — "위험하다"에서 끝나지 않고 "뭘 하면 되는지"까지
    ventilation: dict | None = None   # {"t_vent_est","delta","advice"} — 창문 환기 what-if
    actions: list[str] | None = None  # 등급별 행동 사다리 (위 항목부터 우선)


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


def _solar_factor(
    now: datetime, lat: float | None = None, lon: float | None = None
) -> float:
    """일사 강도 계수 (0~1).

    2026-08-14 개선 — 좌표가 있으면 **실제 태양 위치**(pvlib SPA + Haurwitz 청명모델)로
    계산한다. 기존 "6시 일출~19시 일몰 사인 곡선"은 한여름 한반도에서만 맞는 근사라

      · 계절이 바뀌면 일출·일몰 시각이 어긋나고 (겨울 7시 반 일출을 6시로 봄)
      · 위도가 다르면(서울 vs 제주) 같은 값을 주며
      · 저각도 대기 감쇠를 반영하지 못한다.

    청명 GHI를 900 W/m²(한여름 정오 부근)로 정규화해 0~1로 쓴다.
    좌표가 없거나 계산 실패 시에는 기존 사인 근사로 안전하게 되돌아간다.
    """
    if lat is not None and lon is not None:
        try:
            from app.core.smti import compute_solar_position

            sun = compute_solar_position(lat, lon, now)
            if sun.elevation_deg <= 0.0:
                return 0.0
            return max(0.0, min(1.0, sun.clearsky_ghi / 900.0))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"solar position failed ({type(e).__name__}) — 사인 근사로 대체")

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
    ambient_measured: float | None = None,   # 방 센서 실측 온도 (있으면 추정 대체)
    humidity_measured: float | None = None,  # 방 센서 실측 상대습도 (2026-08-14)
    floor: int | None = None,                # 거주 층 (예: 22)
    total_floors: int | None = None,         # 건물 전체 층수 (예: 25)
    now: datetime | None = None,
    lat: float | None = None,             # 있으면 실제 태양 위치로 일사 계산 (2026-08-14)
    lon: float | None = None,
    facade_gain: float = 1.0,             # 건물 방위 반영 일사 배율 (0.4~1.6, 1.0=미반영)
    facade_note: str | None = None,       # "서향 외피 — 지금 태양과 12° 차이"
) -> IndoorResult:
    now = now or datetime.now(KST)
    sf = _solar_factor(now, lat, lon)
    d = _damping(structure)
    fd = _floor_delta(floor, total_floors, sf, cloud_fraction)

    night_w = 0.0
    if ambient_measured is not None:
        t_in = ambient_measured                    # 실측이 왕 — 층 보정 불필요
        measured = True
    else:
        # ① 축열 감쇠: 실내는 오늘 평균기온 주변에서 바깥 변화를 D만큼만 따라감
        t_in = t_mean_today + d * (t_out_now - t_mean_today)
        # ② 일사 취득: 해가 떠 있고 하늘이 열려 있으면 외피가 데워져 실내로 (+0 ~ +2.6)
        #    2026-08-14 — **건물 방위 반영**(facade_gain). 같은 아파트 같은 층이라도
        #    서향 세대는 여름 저녁에 외피가 달궈지고 북향은 거의 안 받는다. 지금까지는
        #    이 둘을 완전히 같게 봤다. 방위를 못 구하면 1.0이라 기존과 동일하다.
        t_in += sf * (1.0 - cloud_fraction) * (0.8 + 0.25 * building_score) * facade_gain
        # ③ 야간 축열 방출: 해가 진 뒤 낮에 머금은 열 (+1.0 ~ +2.75)
        #    2026-08-14 수정 — 전에는 `sf == 0.0` 일 때만 붙여서 일출 직후 1분 사이에
        #    1.25°C가 절벽처럼 사라졌다(05:59 +1.25 → 06:01 +0.00). 새벽에 앱을 두 번
        #    보면 값이 튀는 원인. 해가 뜬 뒤 서서히 빠지도록 선형 감쇠로 바꾼다.
        night_w = max(0.0, 1.0 - sf / 0.25)   # sf 0 → 1.0, sf 0.25(약 07시) → 0
        t_in += night_w * (1.0 + 0.25 * building_score)
        # ④ 층 위치 보정 (최상층 지붕 일사 / 중간층 완충)
        t_in += fd
        measured = False

    # ④ 실내 체감 = 기온 + 습도(후덥지근함, 야외 엔진과 동일 공식) + 무풍 보정
    #    ⚠️ humidity_pct 는 **외기** 상대습도다. 실내 온도에 그대로 쓰면 안 된다
    #       (2026-08-14 버그: 새벽 외기 89%를 실내 26.7°C에 적용해 체감 +2.6°C 과대).
    #       실측(ambient)이 있어도 방의 수증기압은 외기와 같으므로 동일하게 변환한다.
    #    방 센서(샤오미 LYWSD03MMC 등)가 습도를 실측해 주면 변환이 아예 필요 없다 —
    #    가정을 측정으로 대체한다. 재실자 수분 발생·취사·환기까지 자동 반영된다.
    if humidity_measured is not None:
        rh_in = min(100.0, max(0.0, humidity_measured))
    else:
        rh_in = _indoor_relative_humidity(humidity_pct, t_out_now, t_in)
    wx = WeatherContext(
        temperature_c=t_in, humidity_pct=rh_in,
        wind_speed_ms=0.1, wind_direction_deg=0.0,
    )
    season = wx.season
    dh = _humidity_contribution(t_in, rh_in, season)
    still_air = 0.5 if season == "summer" else 0.0   # 실내 무풍 — 여름 체감 가중
    feel = t_in + dh + still_air

    # ⑤ 위험 등급 (야외와 동일 등급표) + 취약군 앞당김 (레벨당 1.0°C, 상한 3.0)
    shift = min(vulnerability_level(age, conditions) * 1.0, 3.0)
    risk = _classify_risk(feel + shift, season)

    # ⑥ 환기 what-if + 행동 사다리 (2026-08-11, 여름만)
    #    물리: 창문을 열면 실내는 외기온에 근접(잔열 +0.5). 폭염 한낮엔 바깥이 더
    #    더워 "열지 마세요"가 정답인 경우가 많다 — 방향까지 판단해 알려준다.
    #    선풍기: 실내 35°C 이상 고온에선 단독 사용 비권장(온열질환 예방 가이드).
    ventilation: dict | None = None
    actions: list[str] = []
    if season == "summer":
        t_vent = t_out_now + 0.5
        delta = t_vent - t_in
        if delta <= -0.7:
            vent_advice = (
                f"창문을 열면 약 {abs(delta):.1f}도 내려가 실내 {t_vent:.1f}°C 예상 — 환기하세요"
            )
        elif delta >= 0.7:
            vent_advice = "지금은 바깥이 더 더워요 — 창문을 닫고 커튼으로 햇빛을 가리세요"
        else:
            vent_advice = "창문을 열고 닫아도 지금은 큰 차이가 없어요"
        ventilation = {"t_vent_est": round(t_vent, 1), "delta": round(delta, 1), "advice": vent_advice}

        vent_helps = delta <= -0.7
        if risk == "caution":
            actions = ["물을 자주 마시세요"]
            if vent_helps:
                actions.append("창문을 열어 환기하세요")
        elif risk == "warning":
            actions = ["에어컨이 있으면 켜세요"]
            if t_in >= 35.0:
                actions.append("이 온도에선 선풍기만으론 부족해요 — 냉방이 어려우면 무더위쉼터로")
            elif vent_helps:
                actions.append("선풍기를 켜고 창문을 열어 환기하세요")
            else:
                actions.append("선풍기를 켜고 물을 자주 드세요")
        elif risk == "danger":
            actions = [
                "에어컨을 켜세요",
                "냉방이 어려우면 가까운 무더위쉼터로 이동하세요",
                "시원한 물로 손·목을 적셔 체온을 낮추세요",
            ]
        elif risk == "severe":
            actions = [
                "즉시 냉방하거나 무더위쉼터로 이동하세요",
                "어지러움·메스꺼움이 있으면 119에 연락하세요",
            ]

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
            "humidity_pct_out": round(humidity_pct, 1),
            "humidity_pct_in": round(rh_in, 1),
            "humidity_measured": humidity_measured is not None,
            "humidity_delta": round(dh, 1),
            "night_release_w": round(night_w, 2) if not measured else None,
            "vulnerability_shift": shift,
            "floor": floor,
            "floor_delta": round(fd, 1),
            "facade_gain": round(facade_gain, 2),
            "facade_note": facade_note,
        },
        ventilation=ventilation,
        actions=actions or None,
    )
