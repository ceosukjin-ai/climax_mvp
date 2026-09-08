"""Sentinel-2 NDVI → GVI (식생) — Copernicus Data Space Sentinel Hub (2026-09-08).

전세계 나무(GVI) 소스. GSV/SegFormer 대신 위성 NDVI로 식생 지분을 추정한다.
NDVI는 계절 단위 준정적 → 좌표별 장기 캐시(적재 전용으로도 사용 가능).

- 인증: OAuth2 client_credentials (CDSE). 토큰 캐시(~55분).
- NDVI: Statistical API(JSON 반환, 이미지 파싱 불필요). 최근 60일 leastCC 모자이크,
  좌표 주변 ~100m 박스 평균.
- GVI 근사: 위성 NDVI(위에서 본 녹지)를 보행자 GVI(옆에서 본 녹지) 프록시로 사상.
  ⚠️ 방향이 달라 완전 동일하진 않음 — 파일럿 1차 근사, 튜닝 대상.

환경변수: SENTINEL_CLIENT_ID, SENTINEL_CLIENT_SECRET (.env.prod). 없으면 비활성(GVI 0).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"

_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{bands: ["B04", "B08", "dataMask"]}],
    output: [
      {id: "ndvi", bands: 1, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1}
    ]
  };
}
function evaluatePixel(s) {
  let d = s.B08 + s.B04;
  let ndvi = d === 0 ? 0 : (s.B08 - s.B04) / d;
  return {ndvi: [ndvi], dataMask: [s.dataMask]};
}
"""

_client: httpx.AsyncClient | None = None
_token: tuple[float, str] | None = None          # (만료ts, access_token)
_gvi_cache: dict[tuple[float, float], tuple[float, float]] = {}   # key→(ts, gvi)
_GVI_TTL_SEC = 7 * 24 * 3600                       # NDVI 준정적 → 7일 캐시


def enabled() -> bool:
    return bool(os.environ.get("SENTINEL_CLIENT_ID") and os.environ.get("SENTINEL_CLIENT_SECRET"))


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=20.0)
    return _client


async def _get_token() -> str:
    global _token
    if _token is not None and time.time() < _token[0] - 60:
        return _token[1]
    r = await _get_client().post(_TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": os.environ["SENTINEL_CLIENT_ID"],
        "client_secret": os.environ["SENTINEL_CLIENT_SECRET"],
    })
    r.raise_for_status()
    j = r.json()
    tok = j["access_token"]
    _token = (time.time() + float(j.get("expires_in", 600)), tok)
    return tok


def ndvi_to_gvi(ndvi: float) -> float:
    """위성 NDVI → 보행자 GVI 프록시. NDVI 0.2(희박)~0.7(밀생) → 0~0.9 선형, 클램프."""
    g = (ndvi - 0.2) / (0.7 - 0.2) * 0.9
    return max(0.0, min(0.9, g))


async def get_ndvi(lat: float, lon: float, half_deg: float = 0.0006) -> float | None:
    """좌표 주변 ~100m 박스의 평균 NDVI(최근 60일 leastCC). 실패/무자료 시 None."""
    tok = await _get_token()
    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=60)
    body = {
        "input": {
            "bounds": {
                "bbox": [lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg],
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{"type": "sentinel-2-l2a",
                      "dataFilter": {"mosaickingOrder": "leastCC"}}],
        },
        "aggregation": {
            "timeRange": {"from": frm.strftime("%Y-%m-%dT00:00:00Z"),
                          "to": to.strftime("%Y-%m-%dT23:59:59Z")},
            "aggregationInterval": {"of": "P60D"},
            "resx": 10, "resy": 10,
            "evalscript": _EVALSCRIPT,
        },
    }
    r = await _get_client().post(_STATS_URL, json=body,
                                 headers={"Authorization": f"Bearer {tok}"})
    r.raise_for_status()
    data = (r.json() or {}).get("data") or []
    for interval in data:
        stats = (((interval.get("outputs") or {}).get("ndvi") or {})
                 .get("bands") or {}).get("B0", {}).get("stats") or {}
        mean = stats.get("mean")
        if mean is not None and stats.get("sampleCount", 1) > (stats.get("noDataCount", 0)):
            return float(mean)
    return None


async def get_gvi(lat: float, lon: float) -> float | None:
    """좌표 GVI(위성 NDVI 유도). 비활성/실패 시 None → 호출측이 0 등으로 폴백."""
    if not enabled():
        return None
    key = (round(lat, 3), round(lon, 3))
    hit = _gvi_cache.get(key)
    if hit is not None and time.time() - hit[0] < _GVI_TTL_SEC:
        return hit[1]
    try:
        ndvi = await get_ndvi(lat, lon)
    except Exception as e:  # noqa: BLE001
        logger.warning("Sentinel Hub NDVI 실패 ({},{}): {}", lat, lon, e)
        return None
    if ndvi is None:
        return None
    gvi = ndvi_to_gvi(ndvi)
    _gvi_cache[key] = (time.time(), gvi)
    logger.info("[timing] NDVI({:.4f},{:.4f})={:.3f} → GVI {:.3f}", lat, lon, ndvi, gvi)
    return gvi
