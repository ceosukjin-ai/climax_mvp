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
_hourly_cache: dict[tuple[float, float], tuple[float, list]] = {}
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


async def get_observation_at(lat: float, lon: float, when: datetime) -> KMAObservation:
    """**과거 특정 시각**의 기상 (2026-09-17).

    왜 필요한가: /field/check 는 `when` 으로 과거 실측 시각의 **태양**은 복원했지만
    날씨는 여전히 "지금" 것을 썼다. 캡처 복원분(현장에서 저장 못 하고 몇 시간 뒤 업로드)은
    측정 시각 기온이 아니라 업로드 시각 기온으로 엔진이 풀렸다.
    2026-09-16 네 지점이 실측 29.8~30.2 °C 인데 엔진은 넷 다 26.7 °C 였다 — 3.5도 차이가
    통째로 MRT 잔차로 넘어갔다. Tmrt 는 기온 위에 얹히므로 이건 엔진 오차가 아니다.

    Open-Meteo hourly(past_days 최대 92)에서 `when` 에 **가장 가까운 정시**를 고른다.
    2시간 넘게 떨어져 있으면 ValueError — 조용히 엉뚱한 시각을 쓰지 않는다.
    캐시하지 않는다(시각마다 다르고, 검증 경로라 호출이 드물다).
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - when).total_seconds() / 86400.0
    if age_days < -0.5:
        raise ValueError(f"open-meteo: 미래 시각은 조회하지 않는다 ({when.isoformat()})")
    past = max(1, min(92, int(age_days) + 2))
    params = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": "temperature_2m,relative_humidity_2m,"
                  "wind_speed_10m,wind_direction_10m,precipitation",
        "past_days": str(past), "forecast_days": "1",
        "wind_speed_unit": "ms", "timeformat": "unixtime",
    }
    r = await _get_client().get(OPEN_METEO_URL, params=params)
    r.raise_for_status()
    h = (r.json() or {}).get("hourly") or {}
    times = h.get("time") or []
    if not times:
        raise ValueError("open-meteo: hourly 없음")
    target = when.timestamp()
    i = min(range(len(times)), key=lambda k: abs(float(times[k]) - target))
    gap = abs(float(times[i]) - target)
    if gap > 7200.0:
        raise ValueError(f"open-meteo: {when.isoformat()} 근처 자료 없음 (최근접 {gap / 3600:.1f}h)")

    def _f(name, d=0.0):
        try:
            return float((h.get(name) or [])[i])
        except (TypeError, ValueError, IndexError):
            return d

    obs = KMAObservation(
        temperature_c=_f("temperature_2m"),
        humidity_pct=_f("relative_humidity_2m", 60.0),
        wind_speed_ms=_f("wind_speed_10m"),
        wind_direction_deg=_f("wind_direction_10m") % 360.0,
        precipitation_mm=_f("precipitation"),
        observed_at=datetime.fromtimestamp(float(times[i]), tz=timezone.utc),
    )
    logger.info("[open-meteo] 과거기상 ({:.4f},{:.4f}) {} T{:.1f} RH{:.0f} W{:.1f} (요청 {}, 차이 {:.0f}분)",
                lat, lon, obs.observed_at.isoformat(), obs.temperature_c,
                obs.humidity_pct, obs.wind_speed_ms, when.isoformat(), gap / 60)
    return obs


async def get_hourly_air_series(lat: float, lon: float, hours_back: int = 12) -> list:
    """과거 hours_back 시간의 시간별 기온 → [(age_s, temp_c)] **과거→현재** 순.

    열질량 벽온도(estimate_wall_temp_transient)의 forcing 히스토리. 실패 시 [](정상상태 폴백).
    """
    key = (round(lat, 2), round(lon, 2))
    hit = _hourly_cache.get(key)
    if hit is not None and time.time() - hit[0] < _CACHE_TTL_SEC:
        return hit[1]
    params = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": "temperature_2m", "past_days": "1", "forecast_days": "1",
        "timeformat": "unixtime",
    }
    try:
        r = await _get_client().get(OPEN_METEO_URL, params=params)
        r.raise_for_status()
        h = (r.json() or {}).get("hourly") or {}
        times = h.get("time") or []
        temps = h.get("temperature_2m") or []
    except Exception as e:  # noqa: BLE001
        logger.warning("[open-meteo] hourly 실패 ({}): {}", type(e).__name__, e)
        return []
    now = time.time()
    out = []
    for t, tc in zip(times, temps):
        if tc is None:
            continue
        age = now - float(t)
        if -1800.0 <= age <= hours_back * 3600.0 + 1800.0:
            out.append((max(0.0, age), float(tc)))
    out.sort(key=lambda x: -x[0])            # 과거→현재
    _hourly_cache[key] = (now, out)
    return out
