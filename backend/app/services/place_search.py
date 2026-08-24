"""
장소(POI) 검색 — 카카오 로컬 API.

왜 필요한가: 기존 검색은 NCP Geocoding(주소 전용) + OSM Nominatim(한국 POI 거의 없음)
두 단계였다. 사용자가 "서면 스타벅스", "연산엘지아파트" 같은 **상호**를 치면 둘 다 실패한다.
사람은 주소를 외우고 다니지 않으므로, 상호로 찾히지 않으면 목적지 검색은 사실상 동작하지 않는다.

카카오 로컬은 국내 POI 커버리지가 가장 넓고, 무료 한도(일 10만)가 넉넉하다.

  · 키워드 : https://dapi.kakao.com/v2/local/search/keyword.json
  · 주소   : https://dapi.kakao.com/v2/local/search/address.json
  · 역지오 : https://dapi.kakao.com/v2/local/geo/coord2address.json
  · Header : Authorization: KakaoAK {REST_API_KEY}
  · 좌표   : x = 경도(lon), y = 위도(lat)  ← 순서 주의
"""
from __future__ import annotations

import httpx
from loguru import logger

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
COORD2ADDR_URL = "https://dapi.kakao.com/v2/local/geo/coord2address.json"

_TIMEOUT = 6.0


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"KakaoAK {api_key}"}


def _num(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def search_places(
    api_key: str,
    query: str,
    lat: float | None = None,
    lon: float | None = None,
    size: int = 10,
) -> list[dict]:
    """상호·장소명으로 후보 목록을 반환. 실패하면 빈 리스트.

    lat/lon 을 주면 카카오가 그 지점을 기준으로 가까운 곳을 우선 정렬하고,
    응답에 거리(m)가 담긴다 — "가까운 스타벅스"가 위로 올라온다.

    반환 형식은 앱이 그대로 리스트로 그릴 수 있게 평평하게 맞춘다.
    """
    if not api_key or not query.strip():
        return []

    size = max(1, min(size, 15))  # 카카오 상한
    params: dict[str, str] = {"query": query.strip(), "size": str(size)}
    if lat is not None and lon is not None:
        params["x"] = f"{lon:.7f}"
        params["y"] = f"{lat:.7f}"

    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(KEYWORD_URL, params=params, headers=_headers(api_key))
            resp.raise_for_status()
            docs = (resp.json() or {}).get("documents") or []

            # 상호로 안 잡히면 주소로 한 번 더 (도로명·지번을 그대로 친 경우)
            if not docs:
                aresp = await client.get(
                    ADDRESS_URL,
                    params={"query": query.strip(), "size": str(size)},
                    headers=_headers(api_key),
                )
                aresp.raise_for_status()
                for d in (aresp.json() or {}).get("documents") or []:
                    lat_v, lon_v = _num(d.get("y")), _num(d.get("x"))
                    if lat_v is None or lon_v is None:
                        continue
                    out.append({
                        "name": d.get("address_name") or query,
                        "address": (d.get("road_address") or {}).get("address_name")
                                   or d.get("address_name") or "",
                        "category": "주소",
                        "lat": lat_v, "lon": lon_v,
                        "distance_m": None,
                        "source": "kakao_address",
                    })
                return out
    except Exception as e:  # noqa: BLE001
        logger.warning("카카오 장소검색 실패(무시): {}", e)
        return []

    for d in docs:
        lat_v, lon_v = _num(d.get("y")), _num(d.get("x"))
        if lat_v is None or lon_v is None:
            continue
        dist = _num(d.get("distance"))
        out.append({
            "name": d.get("place_name") or query,
            # 도로명이 있으면 도로명, 없으면 지번 — 사람이 알아보는 쪽 우선
            "address": d.get("road_address_name") or d.get("address_name") or "",
            "category": d.get("category_group_name") or "",
            "lat": lat_v,
            "lon": lon_v,
            "distance_m": int(dist) if dist is not None else None,
            "source": "kakao_keyword",
        })
    return out


async def reverse_geocode(
    api_key: str, lat: float, lon: float
) -> str | None:
    """좌표 → 사람이 읽는 주소. 못 찾으면 None.

    ⚠️ 지도에서 찍은 지점의 **이름표**를 만드는 용도일 뿐이다.
    이 함수가 None 을 돌려줘도 경로 계산은 좌표만으로 가능하므로,
    호출부는 실패를 이유로 목적지 선택을 취소해서는 안 된다.
    """
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                COORD2ADDR_URL,
                params={"x": f"{lon:.7f}", "y": f"{lat:.7f}"},
                headers=_headers(api_key),
            )
            resp.raise_for_status()
            docs = (resp.json() or {}).get("documents") or []
    except Exception as e:  # noqa: BLE001
        logger.warning("카카오 역지오코딩 실패(무시): {}", e)
        return None
    if not docs:
        return None
    d = docs[0]
    road = (d.get("road_address") or {}).get("address_name")
    jibun = (d.get("address") or {}).get("address_name")
    return road or jibun or None
