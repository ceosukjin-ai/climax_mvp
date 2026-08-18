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

import asyncio
import math
import time
from dataclasses import asdict, dataclass

import httpx
from loguru import logger

from app.config import get_settings

VWORLD_URL = "https://api.vworld.kr/req/address"
HUB_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
NCP_REVERSE_URL = "https://maps.apigw.ntruss.com/map-reversegeocode/v2/gc"

# 좌표(소수 4자리 ≈ 11m) → 결과 캐시. 건물 정보는 잘 안 변하므로 24시간.
_CACHE: dict[tuple[float, float], tuple[float, "BuildingRisk | None"]] = {}
_CACHE_TTL_SEC = 24 * 3600
# 실패 결과의 캐시 수명. 성공값과 같은 24시간을 쓰면 일시적 타임아웃 한 번이
# 그 좌표의 건물 정보를 하루 종일 막는다(2026-08-11 실사용에서 확인).
_NEG_CACHE_TTL_SEC = 180


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
    if hit is not None:
        ttl = _CACHE_TTL_SEC if hit[1] is not None else _NEG_CACHE_TTL_SEC
        if time.monotonic() - hit[0] < ttl:
            return hit[1]

    result: BuildingRisk | None = None
    for attempt in (1, 2):          # 공공 API가 느릴 때가 잦아 1회 재시도
        try:
            result = await _lookup(lat, lon)
            break
        except Exception as e:  # noqa: BLE001 — 부가 기능: 실패해도 본 기능에 영향 없음
            # 타임아웃 예외는 str(e)가 비어 있어 원인 파악이 안 됐다 → 예외 타입도 남긴다
            logger.warning(
                f"building_risk({lat:.4f},{lon:.4f}) 시도{attempt} 실패: "
                f"{type(e).__name__}: {e or '(메시지 없음)'}"
            )
            if attempt == 2:
                break
            await asyncio.sleep(0.4)

    _CACHE[key] = (time.monotonic(), result)
    return result


# 좌표 → (법정동코드 10자리, 본번, 부번, 주소문자열)
# (법정동코드, 본번, 부번, 주소, 대지구분)
# 대지구분(platGbCd): "0"=대지, "1"=산. 2026-08-18 추가 —
# 이걸 안 보내면 **산번지 건물은 건축물대장이 통째로 안 잡힌다**(부산대 장전동 산30 등).
# 대학·산기슭 주택·요양시설처럼 정작 실내 축이 필요한 곳이 여기 많다.
_Parcel = tuple[str, str, str, str | None, str]


async def _reverse_ncp(client: httpx.AsyncClient, lat: float, lon: float) -> _Parcel | None:
    """NCP 리버스 지오코딩 — 좌표 → 법정동코드·지번.

    실서버(디엔에이클라우드)에서 api.vworld.kr 로 나가는 연결이 ReadTimeout 이라
    이쪽을 1순위로 쓴다. NCP 지도 키는 경로 탐색용으로 이미 설정돼 있고,
    국내 주소 정확도도 V-World 못지않다. (2026-08-11)
    """
    s = get_settings()
    if not s.ncp_maps_client_id or not s.ncp_maps_client_secret:
        logger.warning("리버스지오코딩(NCP) 건너뜀 — NCP_MAPS 키 미설정(.env.prod 확인)")
        return None
    r = await client.get(NCP_REVERSE_URL, params={
        "coords": f"{lon},{lat}", "output": "json", "orders": "addr",
    }, headers={
        "x-ncp-apigw-api-key-id": s.ncp_maps_client_id,
        "x-ncp-apigw-api-key": s.ncp_maps_client_secret,
    })
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    it = results[0]
    bjd_code = (it.get("code") or {}).get("id") or ""     # 법정동코드 10자리
    land = it.get("land") or {}
    n1 = (land.get("number1") or "").strip()              # 본번
    n2 = (land.get("number2") or "").strip()              # 부번
    if len(bjd_code) < 10 or not n1:
        return None
    # NCP land.type: "1"=일반 지번, "2"=산 → 건축HUB platGbCd 는 "0"/"1"
    plat_gb = "1" if str(land.get("type") or "").strip() == "2" else "0"
    reg = it.get("region") or {}
    address = " ".join(
        (reg.get(f"area{i}") or {}).get("name", "") for i in range(1, 5)
    ).strip() + (" 산" if plat_gb == "1" else "") + f" {n1}" + (f"-{n2}" if n2 else "")
    return bjd_code, n1.zfill(4), (n2 or "0").zfill(4), address or None, plat_gb


async def _reverse_vworld(client: httpx.AsyncClient, lat: float, lon: float) -> _Parcel | None:
    """V-World 리버스 지오코딩 — 보조 경로(NCP 실패 시)."""
    s = get_settings()
    if not s.vworld_api_key:
        return None
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
    st = item.get("structure", {})
    bjd_code = st.get("level4LC") or ""
    bunji = (st.get("level5") or "").strip()
    if len(bjd_code) < 10 or not bunji:
        return None
    plat_gb = "1" if "산" in bunji else "0"
    parts = bunji.replace("산", "").split("-")
    return (bjd_code, parts[0].strip().zfill(4),
            (parts[1].strip() if len(parts) > 1 else "0").zfill(4),
            item.get("text"), plat_gb)


async def _lookup(lat: float, lon: float) -> BuildingRisk | None:
    s = get_settings()
    if not s.building_api_key:
        logger.warning("building_risk: BUILDING_API_KEY 미설정")
        return None

    async with httpx.AsyncClient(timeout=9.0) as client:
        # ── ① 리버스 지오코딩: NCP 우선, 실패 시 V-World ──
        parcel = None
        for name, fn in (("NCP", _reverse_ncp), ("V-World", _reverse_vworld)):
            try:
                parcel = await fn(client, lat, lon)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"리버스지오코딩({name}) 실패: {type(e).__name__}: {e or '(메시지 없음)'}")
                parcel = None
            if parcel:
                logger.info(f"리버스지오코딩({name}) 성공: {parcel[3]}")
                break
        if not parcel:
            return None

        # ── ② 건축HUB 건축물대장 표제부: 연식·층수·구조·지붕 ──
        async def _rows(bjd: str, bun_: str, ji_: str, pg: str) -> list | None:
            rb = await client.get(HUB_URL, params={
                "serviceKey": s.building_api_key,
                "sigunguCd": bjd[:5], "bjdongCd": bjd[5:10],
                "platGbCd": pg,
                "bun": bun_, "ji": ji_,
                "numOfRows": "100", "_type": "json",
            })
            rb.raise_for_status()
            body = rb.json().get("response", {}).get("body", {})
            items = body.get("items") or {}
            r = items.get("item") if isinstance(items, dict) else None
            if r is None:
                return None
            if isinstance(r, dict):
                r = [r]
            return r or None

        async def _search(pc: _Parcel) -> list | None:
            """한 지번에 대해 대지구분·부번 변형까지 훑는다."""
            bjd, bun_, ji_, _addr, pg = pc
            other = "0" if pg == "1" else "1"
            for g in (pg, other):                       # 대지 ↔ 산
                for jj in ([ji_] if ji_ == "0000" else [ji_, "0000"]):  # 부번 있는 것 → 본번만
                    found = await _rows(bjd, bun_, jj, g)
                    if found:
                        return found
            return None

        rows = await _search(parcel)
        matched = parcel
        if not rows:
            # 2026-08-18 — **주변 지번까지 훑는다.**
            # GPS는 ±수 m~수십 m 오차가 있고, 리버스지오코딩이 주는 것은
            # '내가 있는 건물의 지번'이 아니라 '좌표에 가장 가까운 지번'이다.
            # 마당·도로·주차장·캠퍼스 통로에 찍히면 그 지번에는 건물이 없다
            # (부산대 연구실 → "장전동 40", 대장 없음. 8/18 실측).
            # 실패했을 때만 도는 경로라 평상시 비용은 0이다.
            seen = {(parcel[0], parcel[1], parcel[2])}
            dlat = 30.0 / 111_320.0
            dlon = 30.0 / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
            for dy, dx in ((dlat, 0.0), (-dlat, 0.0), (0.0, dlon), (0.0, -dlon)):
                near: _Parcel | None = None
                for fn in (_reverse_ncp, _reverse_vworld):
                    try:
                        near = await fn(client, lat + dy, lon + dx)
                    except Exception:  # noqa: BLE001
                        near = None
                    if near:
                        break
                if not near or (near[0], near[1], near[2]) in seen:
                    continue
                seen.add((near[0], near[1], near[2]))
                found = await _search(near)
                if found:
                    rows, matched = found, near
                    logger.info(f"건축물대장: 주변 지번에서 발견 — {near[3]} (좌표 지번은 {parcel[3]})")
                    break
        if not rows:
            logger.info(f"건축물대장 없음: {parcel[3]} — 주변 4방향까지 탐색했으나 건물 없음")
            return None
        address = matched[3]          # 실제로 대장이 잡힌 지번의 주소로 표시한다

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
