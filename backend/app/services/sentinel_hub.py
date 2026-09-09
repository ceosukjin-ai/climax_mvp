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


# NDVI(식생) + NDWI(물) + 광대역 알베도 — 한 번의 Statistical API 호출로 표면 특성 취득.
# 알베도: Sentinel-2 협→광대역 선형식(Bonafoni & Sekertekin 2020 계열).
_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{bands: ["B02","B03","B04","B08","B11","B12","dataMask"]}],
    output: [
      {id: "ndvi", bands: 1, sampleType: "FLOAT32"},
      {id: "ndwi", bands: 1, sampleType: "FLOAT32"},
      {id: "albedo", bands: 1, sampleType: "FLOAT32"},
      {id: "dataMask", bands: 1}
    ]
  };
}
function evaluatePixel(s) {
  let nd = s.B08 + s.B04; let ndvi = nd === 0 ? 0 : (s.B08 - s.B04) / nd;
  let nw = s.B03 + s.B08; let ndwi = nw === 0 ? 0 : (s.B03 - s.B08) / nw;
  let albedo = (0.356*s.B02 + 0.130*s.B04 + 0.373*s.B08
                + 0.085*s.B11 + 0.072*s.B12 - 0.0018) / 1.016;
  return {ndvi: [ndvi], ndwi: [ndwi], albedo: [albedo], dataMask: [s.dataMask]};
}
"""

_client: httpx.AsyncClient | None = None
_token: tuple[float, str] | None = None
_surface_cache: dict[tuple[float, float], tuple[float, dict]] = {}   # key→(ts, surface)
_SURFACE_TTL_SEC = 7 * 24 * 3600                    # 준정적(계절) → 7일 캐시


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


def surface_to_materials(surface: dict) -> tuple[list[tuple[str, float]], float]:
    """위성 표면지수 → SMTI 재질 분율 + 대표 알베도.

    - NDWI>0.2: 수체 → water.
    - 그 외: NDVI로 식생 분율, 나머지 불투수. 불투수 알베도는 픽셀 알베도에서 식생분을
      분리해 산출, asphalt(0.05)~concrete(0.30) 혼합으로 표현(위성 알베도와 정합).
    반환: ([(재질, 분율)...], ground_albedo).
    """
    ndvi = float(surface.get("ndvi") or 0.0)
    ndwi = float(surface.get("ndwi") or 0.0)
    alb = float(surface.get("albedo") or 0.15)
    alb = max(0.02, min(0.6, alb))
    if ndwi > 0.2:
        return [("water", 1.0)], 0.06
    veg = max(0.0, min(0.95, (ndvi - 0.15) / 0.45))
    imp = 1.0 - veg
    mats: list[tuple[str, float]] = []
    if veg > 0.02:
        mats.append(("vegetation", veg))
    if imp > 0.02:
        # 픽셀 알베도에서 식생분(0.20) 제거해 불투수 알베도 A_imp 산출
        a_imp = (alb - veg * 0.20) / imp if imp > 1e-6 else alb
        a_imp = max(0.03, min(0.40, a_imp))
        x = max(0.0, min(1.0, (a_imp - 0.05) / 0.25))   # concrete 비중
        if imp * (1 - x) > 0.01:
            mats.append(("asphalt", imp * (1 - x)))
        if imp * x > 0.01:
            mats.append(("concrete", imp * x))
    if not mats:
        return [("unknown", 1.0)], 0.25
    # 대표 알베도(면적가중, 참고용)
    _A = {"vegetation": 0.20, "asphalt": 0.05, "concrete": 0.30, "water": 0.06, "unknown": 0.25}
    ga = sum(_A.get(m, 0.2) * f for m, f in mats) / max(1e-6, sum(f for _, f in mats))
    return mats, ga


async def get_surface(lat: float, lon: float, half_deg: float = 0.0006) -> dict | None:
    """좌표 주변 ~100m 평균 NDVI·NDWI·알베도(최근 60일 leastCC). 7일 캐시. 실패 시 None."""
    if not enabled():
        return None
    key = (round(lat, 3), round(lon, 3))
    hit = _surface_cache.get(key)
    if hit is not None and time.time() - hit[0] < _SURFACE_TTL_SEC:
        return hit[1]
    try:
        tok = await _get_token()
        to = datetime.now(timezone.utc)
        frm = to - timedelta(days=60)
        body = {
            "input": {
                "bounds": {"bbox": [lon - half_deg, lat - half_deg,
                                    lon + half_deg, lat + half_deg],
                           "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
                "data": [{"type": "sentinel-2-l2a",
                          "dataFilter": {"mosaickingOrder": "leastCC"}}],
            },
            "aggregation": {
                "timeRange": {"from": frm.strftime("%Y-%m-%dT00:00:00Z"),
                              "to": to.strftime("%Y-%m-%dT23:59:59Z")},
                "aggregationInterval": {"of": "P60D"},
                "resx": 10, "resy": 10, "evalscript": _EVALSCRIPT,
            },
        }
        r = await _get_client().post(_STATS_URL, json=body,
                                     headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        data = (r.json() or {}).get("data") or []
        for interval in data:
            outs = (interval.get("outputs") or {})
            def _m(name):
                st = ((outs.get(name) or {}).get("bands") or {}).get("B0", {}).get("stats") or {}
                if st.get("mean") is not None and st.get("sampleCount", 1) > st.get("noDataCount", 0):
                    return float(st["mean"])
                return None
            ndvi = _m("ndvi")
            if ndvi is None:
                continue
            surface = {"ndvi": ndvi, "ndwi": _m("ndwi") or 0.0, "albedo": _m("albedo") or 0.15}
            _surface_cache[key] = (time.time(), surface)
            logger.info("[timing] 위성표면({:.4f},{:.4f}) NDVI{:.2f} NDWI{:.2f} alb{:.2f}",
                        lat, lon, surface["ndvi"], surface["ndwi"], surface["albedo"])
            return surface
    except Exception as e:  # noqa: BLE001
        logger.warning("Sentinel Hub 표면조회 실패 ({},{}): {}", lat, lon, e)
    return None


async def get_gvi(lat: float, lon: float) -> float | None:
    """좌표 GVI(위성 NDVI 유도). 비활성/실패 시 None."""
    s = await get_surface(lat, lon)
    if s is None:
        return None
    return ndvi_to_gvi(s["ndvi"])
