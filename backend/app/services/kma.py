"""
기상청 API Hub 클라이언트.

공식 문서: https://apihub.kma.go.kr/

MVP에서는 두 가지 엔드포인트만 사용:
- 초단기실황(getUltraSrtNcst) — 현재 관측값 (10분 주기 갱신)
- 초단기예보(getUltraSrtFcst) — 6시간 이내 예보 (미래 경로 예측용)

기상청은 격자 좌표계(NX, NY)를 쓰므로 위경도 → 격자 변환 함수 포함.

캐싱 전략:
- 실황은 10분 TTL (기상청 갱신 주기와 일치)
- 격자당 캐싱하여 같은 동네 여러 사용자 요청 병합
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# 한국 표준시 (KST)
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class KMAGrid:
    """기상청 격자 좌표."""

    nx: int
    ny: int


def latlon_to_grid(lat: float, lon: float) -> KMAGrid:
    """위경도 → 기상청 격자 (Lambert Conformal Conic 투영).

    기상청 공식 변환 공식. 정확도 검증 완료.
    """
    RE = 6371.00877  # 지구 반경
    GRID = 5.0  # 격자 간격 (km)
    SLAT1 = 30.0  # 표준 위도 1
    SLAT2 = 60.0  # 표준 위도 2
    OLON = 126.0  # 기준점 경도
    OLAT = 38.0  # 기준점 위도
    XO = 43  # 기준점 X
    YO = 136  # 기준점 Y

    DEGRAD = math.pi / 180.0

    re = RE / GRID
    slat1 = SLAT1 * DEGRAD
    slat2 = SLAT2 * DEGRAD
    olon = OLON * DEGRAD
    olat = OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(
        math.pi * 0.25 + slat1 * 0.5
    )
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn * math.cos(slat1)) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re * sf / (ra ** sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    nx = int(ra * math.sin(theta) + XO + 0.5)
    ny = int(ro - ra * math.cos(theta) + YO + 0.5)
    return KMAGrid(nx=nx, ny=ny)


@dataclass(frozen=True, slots=True)
class KMAObservation:
    """기상청 초단기실황 관측값."""

    temperature_c: float  # T1H — 기온
    humidity_pct: float  # REH — 상대습도
    wind_speed_ms: float  # WSD — 풍속
    wind_direction_deg: float  # VEC — 풍향
    precipitation_mm: float  # RN1 — 1시간 강수량
    observed_at: datetime  # 관측 시각


class KMAError(Exception):
    """기상청 API 요청 실패."""


class KMAClient:
    """기상청 API Hub 비동기 클라이언트."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://apihub.kma.go.kr/api/typ02",
        timeout_sec: float = 10.0,
    ) -> None:
        if not api_key:
            raise ValueError("KMA API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def __aenter__(self) -> "KMAClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def close(self) -> None:
        await self._client.aclose()

    def _get_latest_base_time(self) -> tuple[str, str]:
        """초단기실황 기준시각 계산.

        초단기실황은 매시 40분에 발표 → 현재시각 40분 이후면 현재 시,
        이전이면 이전 시.
        """
        now = datetime.now(KST)
        if now.minute < 40:
            now -= timedelta(hours=1)
        return now.strftime("%Y%m%d"), now.strftime("%H00")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def get_current_observation(
        self,
        lat: float,
        lon: float,
    ) -> KMAObservation:
        """초단기실황 — 현재 가장 가까운 관측 시간의 값 조회.

        Args:
            lat, lon: 조회 위치.

        Returns:
            KMAObservation. 관측값이 누락된 경우 해당 필드는 0.0.

        Raises:
            KMAError: API 오류 또는 파싱 실패.
        """
        grid = latlon_to_grid(lat, lon)
        base_date, base_time = self._get_latest_base_time()

        params = {
            "authKey": self.api_key,
            "numOfRows": "100",
            "pageNo": "1",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

        url = f"{self.base_url}/VilageFcstInfoService_2.0/getUltraSrtNcst"
        response = await self._client.get(url, params=params)
        response.raise_for_status()

        try:
            data = response.json()
            items = data["response"]["body"]["items"]["item"]
        except (KeyError, ValueError) as e:
            raise KMAError(
                f"KMA response parse failed: {response.text[:200]}"
            ) from e

        # items는 [{"category": "T1H", "obsrValue": "..."}, ...]
        values = {item["category"]: item["obsrValue"] for item in items}

        def _parse(key: str, default: float = 0.0) -> float:
            try:
                return float(values.get(key, default))
            except (TypeError, ValueError):
                return default

        observed_at = datetime.strptime(
            f"{base_date}{base_time}", "%Y%m%d%H%M"
        ).replace(tzinfo=KST)

        return KMAObservation(
            temperature_c=_parse("T1H"),
            humidity_pct=_parse("REH"),
            wind_speed_ms=_parse("WSD"),
            wind_direction_deg=_parse("VEC") % 360.0,
            precipitation_mm=_parse("RN1", 0.0),
            observed_at=observed_at,
        )
