"""WBGT(暑さ指数) — 일본·국제 표준 더위 지표 (2026-09-12).

왜: 일본은 체감더위를 WBGT 로 말한다. 환경성이 전국 관측점 WBGT 를 발표하고, 기준을 넘으면
熱中症警戒アラート 가 뜨고, 학교·직장·스포츠 지침이 전부 이 숫자로 움직인다. PET/UTCI 로만 보여주면
"몇이면 위험한지"가 전달되지 않는다. **우리 엔진의 MRT·풍속에서 WBGT 를 산출**해 같이 표기한다.

우리 차별점: 환경성 값은 관측점(광역) 값이라 "이 길이 그늘인가"를 모른다. 우리는 22m 격자에
건물 그늘·노면온도까지 반영하므로 **같은 거리에서도 이쪽 보도와 저쪽 보도가 다른 WBGT** 를 낸다.

구성 (ISO 7243, 옥외·일사 있음):
    WBGT = 0.7·Tnw + 0.2·Tg + 0.1·Ta
  · Tg  흑구온도 — 우리 MRT 에서 역산(표준 150mm 흑구, ε=0.95). MRT 정의식을 Tg 에 대해 수치해.
  · Tnw 자연습구 — 1차로 Stull(2011) 습구온도로 근사. ⚠️ 엄밀한 Tnw(Liljegren)는 일사·풍속에
        더 민감해 보통 약간 높다 → **현재 값은 보수적으로 과소평가 쪽**.
        환경성 관측점 실측과 대조해 보정계수를 정한 뒤 확정할 것(WBGT_TNW_GAIN).
  · Ta  기온.

등급은 환경성 열사병예방정보 기준. 33 이상은 熱中症警戒アラート, 35 이상은 特別警戒アラート 수준.
"""
from __future__ import annotations

import math

GLOBE_D_M = 0.15          # 표준 흑구 지름
GLOBE_EMISS = 0.95
WBGT_TNW_GAIN = 0.0       # 자연습구 보정(실측 대조 전까지 0). Tnw = Tw + GAIN*(Tg - Ta)

_LEVELS = (  # (하한, 코드, 日本語, 한국어, English)
    (31.0, "danger",      "危険",     "위험",     "Danger"),
    (28.0, "severe",      "厳重警戒", "매우 경계", "Extreme caution"),
    (25.0, "warning",     "警戒",     "경계",     "Caution"),
    (21.0, "attention",   "注意",     "주의",     "Attention"),
    (-99.0, "safe",       "ほぼ安全", "대체로 안전", "Almost safe"),
)


def wet_bulb_stull(ta_c: float, rh_pct: float) -> float:
    """습구온도 [°C] — Stull(2011) 경험식. 표준대기압, RH 5~99%·Ta −20~50°C 에서 ±0.3°C."""
    rh = max(1.0, min(100.0, rh_pct))
    return (ta_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
            + math.atan(ta_c + rh) - math.atan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
            - 4.686035)


def globe_temp_from_mrt(mrt_c: float, ta_c: float, v_ms: float) -> float:
    """평균복사온도 → 흑구온도 [°C]. MRT 정의식을 Tg 에 대해 이분법으로 푼다.

    MRT = [ (Tg+273.15)^4 + (1.1e8·v^0.6)/(ε·D^0.4)·(Tg−Ta) ]^0.25 − 273.15
    """
    v = max(0.05, v_ms)
    k = 1.1e8 * v ** 0.6 / (GLOBE_EMISS * GLOBE_D_M ** 0.4)

    def mrt_of(tg: float) -> float:
        inner = (tg + 273.15) ** 4 + k * (tg - ta_c)
        return (max(inner, 1.0)) ** 0.25 - 273.15

    lo, hi = min(ta_c, mrt_c) - 30.0, max(ta_c, mrt_c) + 30.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if mrt_of(mid) < mrt_c:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def wbgt_outdoor(ta_c: float, rh_pct: float, v_ms: float, mrt_c: float) -> dict:
    """옥외 WBGT [°C] 와 구성값. 그늘/볕·건물·노면이 MRT 에 이미 반영돼 있다."""
    tw = wet_bulb_stull(ta_c, rh_pct)
    tg = globe_temp_from_mrt(mrt_c, ta_c, v_ms)
    tnw = tw + WBGT_TNW_GAIN * (tg - ta_c)
    w = 0.7 * tnw + 0.2 * tg + 0.1 * ta_c
    return {"wbgt": round(w, 1), "globe_c": round(tg, 1), "wet_bulb_c": round(tw, 1)}


def wbgt_level(wbgt_c: float) -> dict:
    """환경성 기준 등급 + 알림 여부."""
    for lo, code, ja, ko, en in _LEVELS:
        if wbgt_c >= lo:
            return {"code": code, "ja": ja, "ko": ko, "en": en,
                    "alert": "special" if wbgt_c >= 35.0 else ("alert" if wbgt_c >= 33.0 else None)}
    return {"code": "safe", "ja": "ほぼ安全", "ko": "대체로 안전", "en": "Almost safe", "alert": None}
