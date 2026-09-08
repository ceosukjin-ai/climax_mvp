"""Open-Meteo 전지구 기상 — 해외 좌표 기온·습도·바람 (2026-09-08, 전세계 파일럿).

기상청(KMA)은 한국 격자 전용이라 해외 좌표를 못 준다. 한국 밖 좌표는 Open-Meteo
(전세계 무료·무키)로 현재값을 받아 같은 WeatherContext 로 흘려보낸다. 반환형은
KMAObservation 을 그대로 써서 오케스트레이터 캐시·후처리 경로를 바꾸지 않는다.

- current: temperature_2m, relative_humidity_2m, wind_speed_10m(단위 ms 강제),
  wind_direction_10m, precipitation.
- 10분 인메모리 캐시(반올림 0.05°≈5km) — Redis 스키마(한국 격자)를 건드리지 않는다.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx
from loguru import logger

from app.services.kma import KMAObservation

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_CACHE_TTL_SEC = 600.0
_cache: dict[tuple[float, float], tuple[float, KMAObservation]] = {}
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=8.0)
    return _client


async def get_current_observation(lat: float, lon: float) -> KMAObservation:
    """전지구 현재 기상. 실패 시 예외 → 호출측이 폴백(추정)."""
    key = (round(lat, 2), round(lon, 2))
    hit = _cache.get(key)
    if hit is not None and time.time() - hit[0] < _CACHE_TTL_SEC:
        return hit[1]

    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,relative_humidity_2m,"
                   "wind_speed_10m,wind_direction_10m,precipitation",
        "wind_speed_unit": "ms",
    }
    r = await _get_client().get(OPEN_METEO_URL, params=params)
    r.raise_for_status()
    cur = (r.json() or {}).get("current") or {}
    if "temperature_2m" not in cur:
        raise ValueError(f"open-meteo: current 없음 ({str(cur)[:120]})")

    def _f(v, d=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d

    obs = KMAObservation(
        temperature_c=_f(cur.get("temperature_2m")),
        humidity_pct=_f(cur.get("relative_humidity_2m"), 60.0),
        wind_speed_ms=_f(cur.get("wind_speed_10m")),
        wind_direction_deg=_f(cur.get("wind_direction_10m")) % 360.0,
        precipitation_mm=_f(cur.get("precipitation")),
        observed_at=datetime.now(timezone.utc),
    )
    _cache[key] = (time.time(), obs)
    logger.info("[timing] 기상(Open-Meteo) 조회: ({:.4f},{:.4f}) T{:.1f} RH{:.0f} W{:.1f}",
                lat, lon, obs.temperature_c, obs.humidity_pct, obs.wind_speed_ms)
    return obs
