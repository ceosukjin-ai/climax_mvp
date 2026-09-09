"""외벽(facade) 재질 추정 — 무영상·전지구 파이프라인의 벽재질 스테이지 (2026-09-09).

거리영상 없이 외벽 재질을 정하기 위한 층상(layered) 매핑:
  [기본, 전지구] OSM `building=` 용도 태그 → 재질 클래스
  [정밀, 일본]  PLATEAU 構造種別(RC/SRC/S/W) → 재질 클래스 (OSM 위 오버라이드)
  [명시]        OSM `building:material` 태그가 있으면 최우선

재질 클래스 → 단파 알베도·장파 방사율(외벽 복사 에너지수지 입력). sunlit_frac(태양
마주보는 벽 비율)은 재질이 아니라 기하(캐년/방위) 몫이라 여기서 다루지 않는다.

albedo/emissivity 출처: 도시기상·건물외피 문헌 통용값(콘크리트 α≈0.3, 유리커튼월 α≈0.2·
경면반사 별도, 목재 α≈0.3, 벽돌 α≈0.35, 금속외피 α≈0.5·ε낮음). 경면 유리 반사·방위별
비대칭은 후속 단계에서 정밀화.
"""
from __future__ import annotations

# 재질 클래스 → {albedo(단파 반사), emissivity(장파 방사율)}
MATERIAL_PROPS: dict[str, dict[str, float]] = {
    "glass":    {"albedo": 0.20, "emissivity": 0.90},   # 커튼월 — 경면반사는 후속
    "concrete": {"albedo": 0.30, "emissivity": 0.92},   # RC/SRC, 도시 기본
    "brick":    {"albedo": 0.35, "emissivity": 0.93},   # 벽돌·조적
    "wood":     {"albedo": 0.30, "emissivity": 0.90},   # 목조(일본 주택 다수)
    "metal":    {"albedo": 0.50, "emissivity": 0.30},   # 금속외피 — ε 낮아 야간 덜 식음
    "stone":    {"albedo": 0.33, "emissivity": 0.93},
}
DEFAULT_MATERIAL = "concrete"   # 용도 불명(building=yes 등) 도시 기본 = 현행 하드코딩과 동일

# OSM building= 값 → 재질 클래스. 없는 값은 DEFAULT_MATERIAL.
_OSM_TYPE: dict[str, str] = {
    # 유리·커튼월 경향(현대 상업/업무/대형시설)
    "office": "glass", "commercial": "glass", "hotel": "glass",
    "tower": "glass", "skyscraper": "glass", "public": "glass",
    "government": "glass", "hospital": "glass", "university": "glass",
    "college": "glass", "civic": "glass",
    # 콘크리트(공동주택·산업·학교·종교)
    "apartments": "concrete", "residential": "concrete", "dormitory": "concrete",
    "industrial": "concrete", "warehouse": "concrete", "parking": "concrete",
    "school": "concrete", "kindergarten": "concrete", "church": "concrete",
    "mosque": "concrete", "temple": "concrete", "retail": "concrete",
    "supermarket": "concrete", "garage": "concrete", "garages": "concrete",
    # 목조(단독주택 계열 — 일본 주택 상당수)
    "house": "wood", "detached": "wood", "bungalow": "wood",
    "cabin": "wood", "hut": "wood", "farm": "wood", "farmhouse": "wood",
    # 조적
    "terrace": "brick", "semidetached_house": "brick", "houseboat": "wood",
}

# OSM building:material= 값 → 재질 클래스(명시 태그, 최우선)
_OSM_MATERIAL: dict[str, str] = {
    "glass": "glass", "mirror": "glass",
    "concrete": "concrete", "reinforced_concrete": "concrete", "cement_block": "concrete",
    "brick": "brick", "clay": "brick", "masonry": "brick",
    "wood": "wood", "timber_framing": "wood", "plaster": "wood",
    "metal": "metal", "steel": "metal", "tin": "metal", "aluminium": "metal",
    "stone": "stone", "sandstone": "stone", "granite": "stone", "marble": "stone",
}

# PLATEAU 建築物_構造種別 코드(uro:BuildingStructureType, MLIT 코드리스트 601~) → 재질 클래스.
# 601 목조·토장 / 602 SRC / 603 RC / 604 철골 / 605 경량철골 / 606 조적(연와·블록) /
# 607 보강콘크리트블록 / 608 석조 / 610 기타 / 611 불명
_PLATEAU_STRUCT: dict[str, str] = {
    "601": "wood", "602": "concrete", "603": "concrete",
    "604": "glass", "605": "glass", "606": "brick",
    "607": "concrete", "608": "stone",
}
# 문자 표기(RC/SRC/S/W)도 지원
_PLATEAU_ALPHA: dict[str, str] = {
    "W": "wood", "RC": "concrete", "SRC": "concrete",
    "S": "glass", "LS": "glass", "CB": "brick", "B": "brick", "ST": "stone",
}


def material_from_osm(tags: dict) -> str:
    """OSM 태그 → 재질 클래스. building:material 명시 태그 최우선, 없으면 building= 용도."""
    if not tags:
        return DEFAULT_MATERIAL
    bm = str(tags.get("building:material", "")).strip().lower()
    if bm in _OSM_MATERIAL:
        return _OSM_MATERIAL[bm]
    bt = str(tags.get("building", "")).strip().lower()
    return _OSM_TYPE.get(bt, DEFAULT_MATERIAL)


def material_from_plateau(struct_code: str) -> str | None:
    """PLATEAU 構造種別 코드/문자 → 재질 클래스. 매핑 없으면 None(오버라이드 안 함)."""
    if struct_code is None:
        return None
    s = str(struct_code).strip()
    if s.upper() in _PLATEAU_ALPHA:
        return _PLATEAU_ALPHA[s.upper()]
    return _PLATEAU_STRUCT.get(s)


def props_for(material: str) -> dict[str, float]:
    return MATERIAL_PROPS.get(material, MATERIAL_PROPS[DEFAULT_MATERIAL])


def blend(weighted: list[tuple[str, float]]) -> dict:
    """(재질클래스, 가중치) 목록 → 대표 재질명 + 가중평균 albedo/emissivity.

    가중치는 관측점에서 각 건물의 (높이/거리) 등 복사기여 척도. 최종 벽 복사는 여러 벽의
    혼합이므로 물성은 가중평균, 표시용 이름은 최대가중 클래스.
    """
    if not weighted:
        p = props_for(DEFAULT_MATERIAL)
        return {"material": DEFAULT_MATERIAL, "albedo": p["albedo"],
                "emissivity": p["emissivity"], "mix": {}}
    wsum = sum(max(0.0, w) for _, w in weighted) or 1.0
    alb = emi = 0.0
    mix: dict[str, float] = {}
    for m, w in weighted:
        w = max(0.0, w)
        p = props_for(m)
        alb += p["albedo"] * w
        emi += p["emissivity"] * w
        mix[m] = mix.get(m, 0.0) + w
    dominant = max(mix.items(), key=lambda kv: kv[1])[0]
    return {"material": dominant, "albedo": round(alb / wsum, 3),
            "emissivity": round(emi / wsum, 3),
            "mix": {k: round(v / wsum, 2) for k, v in
                    sorted(mix.items(), key=lambda kv: -kv[1])}}
