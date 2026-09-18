"""일본 暑さ指数(WBGT) — 등급 판정과 조회 (2026-09-18).

일본인은 「暑さ指数 33」을 안다. PET·VPTI 는 모른다. 그래서 일본판은 WBGT 로 말한다.

등급은 **환경성·일본생기상학회 「日常生活に関する指針」** 그대로다. 우리가 정하지 않는다.
    31 이상   危険        외출을 되도록 피하고 시원한 실내로
    28~31     厳重警戒    바깥에서 직사광을 피한다
    25~28     警戒        중간 강도 이상 활동에서 정기적으로 휴식
    21~25     注意        격렬한 운동에서만 위험
    21 미만   ほぼ安全
경보(熱中症警戒情報)는 **예측 WBGT 33 이상**일 때 부현 단위로 환경성이 발표한다.
자료에 따르면 열사병 발생은 WBGT 28 을 넘으면서 뚜렷이 늘어난다.

⚠️ 우리는 경보를 **판정하지 않는다.** 환경성 발표를 그대로 전한다(기상업무법).
   우리가 하는 것은 같은 순간 **그 자리**의 값을 11 m 격자로 내주는 것이다.
"""
from __future__ import annotations

# (하한, 코드, 일본어, 영어, 한국어)
LEVELS = [
    (31.0, "danger",  "危険",      "Danger",        "위험"),
    (28.0, "severe",  "厳重警戒",  "Severe caution", "엄중 경계"),
    (25.0, "warning", "警戒",      "Caution",       "경계"),
    (21.0, "caution", "注意",      "Attention",     "주의"),
    (-99.0, "safe",   "ほぼ安全",  "Mostly safe",   "대체로 안전"),
]

ADVICE = {
    "danger":  {"ja": "外出はなるべく避け、涼しい室内へ",
                "en": "Avoid going out; move somewhere cool",
                "ko": "외출을 피하고 시원한 실내로"},
    "severe":  {"ja": "外では直射日光を避けてください",
                "en": "Stay out of direct sun",
                "ko": "바깥에서 직사광을 피하세요"},
    "warning": {"ja": "こまめに休憩と水分を",
                "en": "Rest and hydrate regularly",
                "ko": "자주 쉬고 물을 드세요"},
    "caution": {"ja": "激しい運動のときは注意",
                "en": "Care during hard exercise",
                "ko": "격렬한 운동 시 주의"},
    "safe":    {"ja": "ふだん通りで大丈夫です",
                "en": "Normal activity is fine",
                "ko": "평소대로 괜찮습니다"},
}

# 취약도 → 등급 경계를 낮춘다 (몸씨 BodyRiskEngine 과 같은 값).
# 심혈관 +2 / 호흡기·당뇨·신장 +1 / 임신 +1 / 75세↑·4세↓ +2 / 65세↑ +1, 상한 3.
# 같은 WBGT 라도 고령자·질환자는 더 위험하다. PET 값을 바꾸지 않고 **판정만** 옮긴다 —
# 값을 개인마다 바꾸면 같은 화면을 두 사람이 다르게 읽는다.
VULN_SHIFT_PER_LEVEL = 1.0      # 레벨당 경계 1.0 ℃ 하향
VULN_SHIFT_MAX = 3.0


def vulnerability_level(age: int | None, conditions: list[str] | None) -> int:
    lvl = 0
    c = set(conditions or ())
    if "cardio" in c:
        lvl += 2
    if c & {"resp", "diabetes", "kidney"}:
        lvl += 1
    if "pregnant" in c:
        lvl += 1
    if age is not None:
        lvl += 2 if (age >= 75 or age <= 4) else (1 if age >= 65 else 0)
    return min(lvl, 3)


def level_of(wbgt: float, age: int | None = None,
             conditions: list[str] | None = None) -> dict:
    """WBGT → 등급. 취약도가 있으면 경계를 낮춘다(더 일찍 경고)."""
    shift = min(vulnerability_level(age, conditions) * VULN_SHIFT_PER_LEVEL, VULN_SHIFT_MAX)
    for lo, code, ja, en, ko in LEVELS:
        if wbgt >= lo - shift:
            return {"code": code, "ja": ja, "en": en, "ko": ko,
                    "threshold": lo, "vuln_shift": round(shift, 1),
                    "advice": ADVICE[code]}
    return {"code": "safe", "ja": "ほぼ安全", "en": "Mostly safe", "ko": "대체로 안전",
            "threshold": -99.0, "vuln_shift": round(shift, 1), "advice": ADVICE["safe"]}


ATTRIBUTION = {
    "ja": "出典：環境省熱中症予防情報サイト",
    "en": "Source: Ministry of the Environment, Japan — Heat Illness Prevention Site",
    "ko": "출처: 일본 환경성 열사병 예방정보 사이트",
}
