"""
건물 열취약 판정 서비스 — 실내축 2단계 (2026-08-10).

좌표 → (V-World 리버스 지오코딩) 지번 주소·법정동코드
     → (건축HUB 건축물대장 표제부) 연식·층수·구조·지붕
     → 폭염 실내 과열 취약 점수 (heuristic v1)

- 국가 공개 데이터만 사용: V-World(국토부), 건축물대장(건축HUB). 모두 무료.
- 개인정보 없음 — 좌표는 조회에만 사용하고 저장하지 않는다.
- 환경변수(.env): VWORLD_API_KEY, BUILDING_API_KEY

점수 근거(v1, 문헌·상식 기반 — 추후 이어러블 실측으로 보정 학습 예정):
  · 준공연도: 오래될수록 단열 기준 이전 → 실내 과열 취약
  · 구조: 목·벽돌·블록·조적 → 축열/단열 불리
  · 지붕: 슬레이트 → 여름 복사열 취약(노후 주거 지표)
  · 층수: 저층(≤3층) → 지붕 일사의 실내 영향 큼
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import httpx
from loguru import logger

from app.config import get_settings

VWORLD_URL = "https://api.vworld.kr/req/address"
HUB_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

# 좌표(소수 4자리 ≈ 11m) → 결과 캐시. 건물 정보는 잘 안 변하므로 24시간.
_CACHE: dict[tuple[float, float], tuple[float, "BuildingRisk | None"]] = {}
_CACHE_TTL_SEC = 24 * 3600


@dataclass
class BuildingRisk:
    address: str | None        # 지번 주소
    building_name: str | None  # 건물명 (있으면)
    built_year: int | None     # 준공(사용승인) 연도
    floors: int | None         # 지상 층수
    structure: str | None      # 구조 (철근콘크리트 등)
    roof: str | None           # 지붕
    purpose: str | None        # 주용도
    score: int                 # 취약 점수 (0~7)
    level: str                 # low / mid / high
    reasons: list[str]         # 판정 근거 (화면 표시용)


async def building_risk(lat: float, lon: float) -> BuildingRisk | None:
    """좌표의 건물 열취약 판정. 실패(주소 없음·API 오류)면 None — 호출측은 무시하면 됨."""
    key = (round(lat, 4), round(lon, 4))
    hit = _CACHE.get(key)
    if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL_SEC:
        return hit[1]

    result: BuildingRisk | None = None
    try:
        result = await _lookup(lat, lon)
    except Exception as e:  # noqa: BLE001 — 부가 기능: 실패해도 본 기능에 영향 없음
        logger.warning(f"building_risk({lat:.4f},{lon:.4f}) failed: {e}")

    _CACHE[key] = (time.monotonic(), result)
    return result


async def _lookup(lat: float, lon: float) -> BuildingRisk | None:
    s = get_settings()
    if not s.vworld_api_key or not s.building_api_key:
        logger.warning("building_risk: VWORLD_API_KEY/BUILDING_API_KEY 미설정")
        return None

    async with httpx.AsyncClient(timeout=5.0) as client:
        # ── ① V-World 리버스 지오코딩: 좌표 → 지번 주소 + 법정동코드 ──
        rv = await client.get(VWORLD_URL, params={
            "service": "address", "request": "getAddress", "version": "2.0",
            "crs": "epsg:4326", "point": f"{lon},{lat}", "format": "json",
            "type": "PARCEL", "key": s.vworld_api_key,
        })
        rv.raise_for_status()
        vj = rv.json().get("response", {})
        if vj.get("status") != "OK":
            return None
        item = (vj.get("result") or [{}])[0]
        address = item.get("text")
        st = item.get("structure", {})
        bjd_code = st.get("level4LC") or ""     # 법정동코드 10자리
        bunji = (st.get("level5") or "").strip()  # 예: "200" / "123-45"
        if len(bjd_code) < 10 or not bunji:
            return None
        parts = bunji.replace("산", "").split("-")
        bun = parts[0].strip().zfill(4)
        ji = (parts[1].strip() if len(parts) > 1 else "0").zfill(4)

        # ── ② 건축HUB 건축물대장 표제부: 연식·층수·구조·지붕 ──
        rb = await client.get(HUB_URL, params={
            "serviceKey": s.building_api_key,
            "sigunguCd": bjd_code[:5], "bjdongCd": bjd_code[5:10],
            "bun": bun, "ji": ji,
            "numOfRows": "100", "_type": "json",
        })
        rb.raise_for_status()
        body = rb.json().get("response", {}).get("body", {})
        items = body.get("items") or {}
        rows = items.get("item") if isinstance(items, dict) else None
        if rows is None:
            return None
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            return None

        # 한 지번에 여러 동(아파트 단지 등) — 가장 높은 동을 대표로
        def _floors(r: dict) -> int:
            try:
                return int(r.get("grndFlrCnt") or 0)
            except (TypeError, ValueError):
                return 0
        top = max(rows, key=_floors)

    built_year: int | None = None
    apr = str(top.get("useAprDay") or "")
    if len(apr) >= 4 and apr[:4].isdigit():
        built_year = int(apr[:4])
    floors = _floors(top) or None
    structure = (top.get("strctCdNm") or "").strip() or None
    roof = (top.get("roofCdNm") or "").strip() or None
    purpose = (top.get("mainPurpsCdNm") or "").strip() or None
    name = (top.get("bldNm") or "").strip() or None

    # ── ③ 취약 점수 (v1 heuristic) ──
    score = 0
    reasons: list[str] = []
    if built_year is not None:
        if built_year < 1980:
            score += 3
            reasons.append(f"{built_year}년 준공 — 단열 기준 이전 노후 건물")
        elif built_year < 1990:
            score += 2
            reasons.append(f"{built_year}년 준공 — 노후 건물")
        elif built_year < 2005:
            score += 1
            reasons.append(f"{built_year}년 준공")
    if structure and any(k in structure for k in ("목", "벽돌", "블록", "조적", "시멘트")):
        score += 1
        reasons.append(f"{structure} — 단열에 불리한 구조")
    if roof and "슬레이트" in roof:
        score += 2
        reasons.append(f"{roof} 지붕 — 여름 복사열 취약")
    if floors is not None and floors <= 3:
        score += 1
        reasons.append(f"저층({floors}층) — 지붕 일사 영향 큼")

    level = "high" if score >= 4 else ("mid" if score >= 2 else "low")
    return BuildingRisk(
        address=address, building_name=name, built_year=built_year,
        floors=floors, structure=structure, roof=roof, purpose=purpose,
        score=score, level=level, reasons=reasons,
    )


def to_dict(b: BuildingRisk) -> dict:
    return asdict(b)
