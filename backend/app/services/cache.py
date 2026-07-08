"""
Redis 캐시 계층.

키 설계:
    pano:analysis:{pano_id}       → 공간 지수 (VSI 구성요소 + 재질)  영구
    pano:location:{lat}:{lon}     → 좌표 → panoId 매핑          TTL 30일
    weather:kma:{nx}:{ny}         → 기상청 격자 관측값            TTL 600s

왜 좌표 → panoId 매핑이 필요한가:
    Google Metadata API도 요청마다 돈은 안 들지만 호출 대기시간이 있음.
    근처 좌표가 같은 panoId를 공유하므로, 격자 단위 캐싱으로 재조회 회피.

JSON 직렬화는 orjson 사용 (기본 json 대비 수배 빠름).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import orjson
import redis.asyncio as redis
from loguru import logger


@dataclass(frozen=True, slots=True)
class PanoAnalysisCache:
    """Redis에 저장될 panoId 단위 분석 결과."""

    pano_id: str
    lat: float
    lon: float
    svf: float
    gvi: float
    bvi: float
    material_ratios: dict[str, float]
    capture_date: str | None
    computed_at: str  # ISO 8601
    # 도로축 — panoId 단위로 miss 시 1회 계산해 영구 캐시(PWI Δθ용).
    # 기존(구버전) 캐시 항목엔 없으므로 기본값을 둔다(from_bytes 하위호환).
    road_axis_deg: float = 0.0
    road_axis_source: str = "assumed"  # osm | gps | assumed

    def to_bytes(self) -> bytes:
        return orjson.dumps(asdict(self))

    @classmethod
    def from_bytes(cls, data: bytes) -> "PanoAnalysisCache":
        parsed = orjson.loads(data)
        return cls(**parsed)


@dataclass(frozen=True, slots=True)
class WeatherCache:
    """기상청 격자당 관측값 캐시."""

    nx: int
    ny: int
    temperature_c: float
    humidity_pct: float
    wind_speed_ms: float
    wind_direction_deg: float
    precipitation_mm: float
    observed_at: str
    cached_at: str

    def to_bytes(self) -> bytes:
        return orjson.dumps(asdict(self))

    @classmethod
    def from_bytes(cls, data: bytes) -> "WeatherCache":
        parsed = orjson.loads(data)
        return cls(**parsed)


class CacheService:
    """Redis 래퍼. 앱 수명 동안 단일 pool 공유."""

    def __init__(self, redis_url: str) -> None:
        self._pool = redis.ConnectionPool.from_url(
            redis_url, decode_responses=False, max_connections=50
        )
        self._client = redis.Redis(connection_pool=self._pool)

    async def ping(self) -> bool:
        try:
            return await self._client.ping()
        except redis.ConnectionError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
        await self._pool.aclose()

    # ===== panoId 단위 공간 분석 =====

    @staticmethod
    def _pano_analysis_key(pano_id: str) -> str:
        return f"pano:analysis:{pano_id}"

    async def get_pano_analysis(
        self, pano_id: str
    ) -> PanoAnalysisCache | None:
        raw = await self._client.get(self._pano_analysis_key(pano_id))
        if raw is None:
            return None
        try:
            return PanoAnalysisCache.from_bytes(raw)
        except Exception as e:
            logger.warning("Failed to decode pano cache for {}: {}", pano_id, e)
            return None

    async def set_pano_analysis(self, analysis: PanoAnalysisCache) -> None:
        """영구 저장 (TTL 없음). 같은 panoId는 영상이 재촬영되기 전엔 안 바뀜."""
        await self._client.set(
            self._pano_analysis_key(analysis.pano_id),
            analysis.to_bytes(),
        )

    # ===== 좌표 → panoId 매핑 =====

    @staticmethod
    def _location_key(lat: float, lon: float, precision: int = 5) -> str:
        """위경도 반올림 → 격자 단위 키.

        precision=5 → 약 1m 단위, 4 → 약 11m, 3 → 약 110m.
        VSI 분석 단위 (25m)보다 작게 잡아 안전.
        """
        return f"pano:location:{round(lat, precision)}:{round(lon, precision)}"

    async def get_pano_id_for_location(
        self, lat: float, lon: float
    ) -> str | None:
        raw = await self._client.get(self._location_key(lat, lon))
        return raw.decode() if raw else None

    async def set_pano_id_for_location(
        self, lat: float, lon: float, pano_id: str
    ) -> None:
        # 30일 TTL — Google이 panoId를 교체할 수 있으므로 영구는 과함
        await self._client.setex(
            self._location_key(lat, lon), 60 * 60 * 24 * 30, pano_id
        )

    # ===== 기상 캐시 =====

    @staticmethod
    def _weather_key(nx: int, ny: int) -> str:
        return f"weather:kma:{nx}:{ny}"

    async def get_weather(self, nx: int, ny: int) -> WeatherCache | None:
        raw = await self._client.get(self._weather_key(nx, ny))
        if raw is None:
            return None
        try:
            return WeatherCache.from_bytes(raw)
        except Exception as e:
            logger.warning("Failed to decode weather cache: {}", e)
            return None

    async def set_weather(
        self, weather: WeatherCache, ttl_seconds: int = 600
    ) -> None:
        await self._client.setex(
            self._weather_key(weather.nx, weather.ny),
            ttl_seconds,
            weather.to_bytes(),
        )

    # ===== 디버깅·관리 =====

    async def count_pano_cache(self) -> int:
        """현재 캐시된 panoId 수. 모니터링용."""
        count = 0
        async for _ in self._client.scan_iter(match="pano:analysis:*", count=1000):
            count += 1
        return count

    async def clear_namespace(self, pattern: str) -> int:
        """패턴 매칭 키 전체 삭제 (개발용)."""
        deleted = 0
        async for key in self._client.scan_iter(match=pattern, count=1000):
            await self._client.delete(key)
            deleted += 1
        return deleted
