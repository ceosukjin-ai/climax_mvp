"""일본 주택 — 단열 시기·구조로 본 「우리 집 더위」 (2026-09-25).

왜: 일본 주택 약 5,400만 호 중 무단열 24% · S55 22% · H4 36% · 현행기준 18% (令和4年度, 国交省).
도쿄 23구 열사병 사망은 거의 전부 실내다(2024 여름 306명 중 291명, 監察医務院). 한국처럼 건물대장이
공개돼 있지 않고, 도쿄 PLATEAU 공개판에는 건축연도·구조가 0% 다(2026-09-25 표본 10.8만 동 확인).
→ **그 집 한 채**의 築年·構造 를 사용자에게 한 번 묻는다(기기 안에만 저장). 모르면 통계 기본값.

계수 출처 (6지역 = 東京·大阪·名古屋 등)
  · 等級4(H11/H28): UA 0.87, ηAC 2.8 / 等級3(H4): UA 1.54, ηAC 3.8   — 国交省 断熱等性能等級 基準値
  · 等級2(S55): UA 1.67                                               — 省エネ基準 변천(ホームズ君, Q値 4.8)
  · 等級2 ηAC · 無断熱 UA·ηAC : **문헌값 미확보 — 가정값(UNCONFIRMED)**. 등급 순서만 지키게 잡았다.
BTLI(btli.facade_load)에 u_override=UA, solar_frac=ηAC/100 로 넣어 **같은 집 모양에서 등급만 바꾼 부하**를
등급4 대비 비율로 낸다. 절대 kWh 가 아니라 "현행 기준 집보다 몇 배 더워지기 쉬운가" 다.
"""
from __future__ import annotations

import json
import math
import os

ERAS = {
    # key: (라벨ja, 등급, UA, ηAC, 출처확실?)
    "pre1980":  ("1980年以前（無断熱が多い）", 1, 2.2, 4.5, False),   # UNCONFIRMED 가정값
    "s55":      ("1980〜1991年（旧省エネ基準）", 2, 1.67, 4.5, False),  # UA 문헌, ηAC 가정
    "h4":       ("1992〜1998年（新省エネ基準）", 3, 1.54, 3.8, True),
    "h11":      ("1999年以降（次世代・現行基準）", 4, 0.87, 2.8, True),
}
# 모를 때: 전국 주택 스톡 비율(令和4年度) — 기대값이 아니라 **분포**로 준다.
STOCK_2022 = {"pre1980": 0.24, "s55": 0.22, "h4": 0.36, "h11": 0.18}

# 대도시별 기본값 (令和5年 住宅・土地統計調査 7-1 → scripts/load_jp_housing_prior.py) — 있으면 이것을 쓴다.
_PRIOR_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "jp_housing_prior_2023.json")  # app/data
CITY = {"区部": (35.69, 139.69), "横浜市": (35.45, 139.64), "川崎市": (35.53, 139.70),
        "大阪市": (34.69, 135.50), "名古屋市": (35.18, 136.91), "京都市": (35.01, 135.77),
        "神戸市": (34.69, 135.20), "福岡市": (33.59, 130.40), "札幌市": (43.06, 141.35),
        "仙台市": (38.27, 140.87), "広島市": (34.39, 132.46), "さいたま市": (35.86, 139.65),
        "千葉市": (35.61, 140.12)}
_prior_cache: dict | None = None


def _prior(lat: float, lon: float, structure: str | None) -> tuple[dict, str]:
    """(시기 분포, 출처 라벨). 대도시 30 km 안이면 그 도시, 아니면 전국, 파일이 없으면 令和4 스톡."""
    global _prior_cache
    if _prior_cache is None:
        try:
            _prior_cache = json.load(open(_PRIOR_PATH, encoding="utf-8")).get("areas", {})
        except (OSError, ValueError):
            _prior_cache = {}
    areas = _prior_cache
    if areas:
        best, bd = None, 1e9
        for key, (la, lo) in CITY.items():
            d = math.hypot((la - lat) * 111.0, (lo - lon) * 111.0 * math.cos(math.radians(lat)))
            if d < bd:
                best, bd = key, d
        name = next((a for a in areas if best and best in a), None) if bd < 30 else None
        name = name or next((a for a in areas if "全国" in a), None)
        if name:
            row = areas[name].get(structure or "all") or areas[name].get("all")
            if row:
                return {k: row[k] for k in STOCK_2022}, f"住宅・土地統計調査2023 {name}"
    return dict(STOCK_2022), "住宅ストック 令和4年度（全国）"


# 집 모양 대표값 (UNCONFIRMED 근사) — 등급 간 비교용이라 모양은 같게만 두면 된다.
SHAPES = {
    "detached": dict(footprint_area_m2=60.0, floors=2, is_slab=False),      # 戸建て
    "apartment": dict(footprint_area_m2=400.0, floors=5, is_slab=True),     # 集合
}
STRUCT_NOTE = {
    "wood": {"ja": "木造は熱をためにくく、日中の暑さがすぐ室内に伝わります。",
             "ko": "목조는 열을 저장하지 못해 낮 더위가 곧바로 실내로 들어옵니다.",
             "en": "Wooden houses store little heat, so daytime heat reaches the rooms quickly."},
    "steel": {"ja": "鉄骨造は外壁が薄いことが多く、西日の影響を受けやすい傾向があります。",
              "ko": "철골조는 외벽이 얇은 경우가 많아 서쪽 햇볕 영향을 받기 쉽습니다.",
              "en": "Steel-frame homes often have thin walls and pick up afternoon sun."},
    "rc": {"ja": "鉄筋コンクリートは熱をためるため、夜になっても室内が冷えにくいことがあります。",
           "ko": "철근콘크리트는 열을 저장해 밤에도 실내가 잘 식지 않을 수 있습니다.",
           "en": "Concrete stores heat, so rooms may stay warm into the night."},
}
ADVICE = {
    1: {"ja": "断熱がほとんどない可能性があります。暑い日は室内でも熱中症の危険があります。エアコンを我慢せず使ってください。",
        "ko": "단열이 거의 없을 수 있습니다. 더운 날에는 실내에서도 열사병 위험이 있습니다. 에어컨을 참지 말고 켜세요.",
        "en": "Your home may have little or no insulation. Heatstroke can happen indoors — do not hold back on the air conditioner."},
    2: {"ja": "断熱は弱めです。日中は窓の外側で日差しをさえぎり、エアコンを早めに。",
        "ko": "단열이 약한 편입니다. 낮에는 창 바깥에서 햇볕을 가리고 에어컨을 일찍 켜세요.",
        "en": "Insulation is weak. Block sun outside the windows and switch the AC on early."},
    3: {"ja": "一定の断熱があります。西日の入る部屋に注意。",
        "ko": "어느 정도 단열이 있습니다. 서쪽 햇볕이 드는 방을 주의하세요.",
        "en": "Some insulation. Watch rooms that get afternoon sun."},
    4: {"ja": "現行の省エネ基準相当です。",
        "ko": "현행 에너지 기준 수준입니다.",
        "en": "Meets the current energy standard."},
}


def _load(era: str, kind: str, lat: float, lon: float) -> float:
    from app.services.btli import facade_load
    _lab, _g, ua, eta, _ok = ERAS[era]
    r = facade_load(lat=lat, lon=lon, material_base="concrete", material_new="concrete",
                    u_override=ua, solar_frac=eta / 100.0, **SHAPES[kind])
    return float(r["load_base_w"]), float(r["envelope_base_w"])


def home(lat: float, lon: float, era: str | None, structure: str | None,
         kind: str | None, lang: str = "ja") -> dict:
    kind = kind if kind in SHAPES else "detached"
    ref_tot, ref_env = _load("h11", kind, lat, lon)
    out_eras = []
    for k in ERAS:
        tot, env = _load(k, kind, lat, lon)
        out_eras.append({"era": k, "label": ERAS[k][0], "grade": ERAS[k][1],
                         "ua": ERAS[k][2], "eta_ac": ERAS[k][3], "confirmed": ERAS[k][4],
                         "load_vs_grade4": round(tot / ref_tot, 2),
                         "envelope_vs_grade4": round(env / ref_env, 2)})
    known = era in ERAS
    res = {"ok": True, "kind": kind, "region_assumed": 6, "input": {"era": era, "structure": structure},
           "eras": out_eras,
           "note": {"ja": "同じ形の家で断熱の時期だけを変えた比較です（現行基準=1.0）。地域区分6（東京・大阪など）の値。",
                    "ko": "같은 모양의 집에서 단열 시기만 바꾼 비교입니다(현행 기준=1.0). 6지역(도쿄·오사카 등) 기준.",
                    "en": "Same house shape, only the insulation era changes (current standard = 1.0). Climate zone 6."}[lang if lang in ("ja", "ko", "en") else "ja"]}
    L = lang if lang in ("ja", "ko", "en") else "ja"
    if known:
        e = next(x for x in out_eras if x["era"] == era)
        res.update(estimated=False, grade=e["grade"], load_vs_grade4=e["load_vs_grade4"],
                   advice=ADVICE[e["grade"]][L])
    else:
        # 모르면 스톡 분포로 기대값과 "무단열일 확률" 을 같이 준다.
        pri, src = _prior(lat, lon, structure)
        exp = sum(pri[x["era"]] * x["load_vs_grade4"] for x in out_eras)
        res.update(estimated=True, grade=None, load_vs_grade4=round(exp, 2),
                   p_uninsulated=round(pri["pre1980"], 3), prior=pri, prior_source=src,
                   advice={"ja": "築年数を入れると、あなたの家に合わせて判定します（全国の住宅の約4軒に1軒は無断熱）。",
                           "ko": "지은 해를 입력하면 이 집에 맞춰 판정합니다(일본 주택 약 4채 중 1채는 무단열).",
                           "en": "Enter when your home was built for a better estimate (about 1 in 4 homes in Japan has no insulation)."}[L])
    if structure in STRUCT_NOTE:
        res["structure_note"] = STRUCT_NOTE[structure][L]
    res["sources"] = ["国交省 断熱等性能等級 基準値（等級3・4）", "省エネ基準の変遷（S55 UA 1.67）",
                      "住宅ストック断熱割合 令和4年度（無断熱24%）"]
    res["unconfirmed"] = "等級2のηAC・無断熱のUA/ηACは文献値未確保の仮定値"
    return res
