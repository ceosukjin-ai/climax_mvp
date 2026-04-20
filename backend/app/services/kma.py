"""
기상청 API Hub 클라이언트.

세 가지 API 지원:
1. 초단기실황 (getUltraSrtNcst) — 현재 이 순간 실측값
2. 초단기예보 (getUltraSrtFcst) — 앞으로 6시간 이내 (1시간 단위)
3. 단기예보   (getVilageFcst)   — 앞으로 3일 이내 (3시간 단위)

세 API 모두 위경도 → 격자(NX, NY) 변환 후 조회.
인증키는 공통.
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

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True, slots=True)
class KMAGrid:
    nx: int
    ny: int


def latlon_to_grid(lat: float, lon: float) -> KMAGrid:
    """위경도 → 기상청 격자 (Lambert Conformal Conic 투영)."""
    RE = 6371.00877
    GRID = 5.0
    SLAT1 = 30.0
    SLAT2 = 60.0
    OLON = 126.0
    OLAT = 38.0
    XO = 43
    YO = 136

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
    """초단기실황 — 현재 실측 관측값."""

    temperature_c: float
    humidity_pct: float
    wind_speed_ms: float
    wind_direction_deg: float
    precipitation_mm: float
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class KMAForecast:
    """초단기예보 또는 단기예보 한 시점의 값."""

    forecast_for: datetime           # 예보 대상 시각
    temperature_c: float | None
    humidity_pct: float | None
    wind_speed_ms: float | None
    wind_direction_deg: float | None
    precipitation_mm: float | None
    sky_condition: str | None        # 맑음/구름많음/흐림
    precipitation_type: str | None   # 없음/비/비눈/눈/소나기


class KMAError(Exception):
    pass


def _parse_float(val: str | None, default: float | None = None) -> float | None:
    if val is None or val in ("강수없음", "-", "", "적설없음"):
        return 0.0 if default is None else default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


SKY_CODE = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY_CODE = {
    "0": "없음", "1": "비", "2": "비/눈", "3": "눈",
    "4": "소나기", "5": "빗방울", "6": "빗방울눈날림", "7": "눈날림",
}


class KMAClient:
    """기상청 API Hub 비동기 클라이언트."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://apihub.kma.go.kr/api/typ02/openApi",
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

    # ===== 초단기실황 =====

    def _get_ncst_base_time(self) -> tuple[str, str]:
        """초단기실황 기준시각 — 매시 40분 후 현재 시, 전이면 이전 시."""
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
        self, lat: float, lon: float
    ) -> KMAObservation:
        grid = latlon_to_grid(lat, lon)
        base_date, base_time = self._get_ncst_base_time()

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
                f"KMA ncst parse failed: {response.text[:200]}"
            ) from e

        values = {item["category"]: item["obsrValue"] for item in items}

        observed_at = datetime.strptime(
            f"{base_date}{base_time}", "%Y%m%d%H%M"
        ).replace(tzinfo=KST)

        return KMAObservation(
            temperature_c=_parse_float(values.get("T1H"), 0.0) or 0.0,
            humidity_pct=_parse_float(values.get("REH"), 0.0) or 0.0,
            wind_speed_ms=_parse_float(values.get("WSD"), 0.0) or 0.0,
            wind_direction_deg=(_parse_float(values.get("VEC"), 0.0) or 0.0) % 360.0,
            precipitation_mm=_parse_float(values.get("RN1"), 0.0) or 0.0,
            observed_at=observed_at,
        )

    # ===== 초단기예보 (앞으로 6시간, 1시간 단위) =====

    def _get_usrt_fcst_base_time(self) -> tuple[str, str]:
        """초단기예보 기준시각 — 매시 30분 발표, 45분 후 사용 가능."""
        now = datetime.now(KST)
        if now.minute < 45:
            now -= timedelta(hours=1)
        return now.strftime("%Y%m%d"), now.strftime("%H30")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def get_ultra_short_forecast(
        self, lat: float, lon: float
    ) -> list[KMAForecast]:
        """앞으로 6시간 이내 예보 (1시간 단위)."""
        grid = latlon_to_grid(lat, lon)
        base_date, base_time = self._get_usrt_fcst_base_time()

        params = {
            "authKey": self.api_key,
            "numOfRows": "200",
            "pageNo": "1",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

        url = f"{self.base_url}/VilageFcstInfoService_2.0/getUltraSrtFcst"
        response = await self._client.get(url, params=params)
        response.raise_for_status()

        try:
            data = response.json()
            items = data["response"]["body"]["items"]["item"]
        except (KeyError, ValueError) as e:
            raise KMAError(
                f"KMA ultraSrtFcst parse failed: {response.text[:200]}"
            ) from e

        # fcstDate + fcstTime 으로 그룹핑
        grouped: dict[tuple[str, str], dict[str, str]] = {}
        for item in items:
            key = (item["fcstDate"], item["fcstTime"])
            grouped.setdefault(key, {})[item["category"]] = item["fcstValue"]

        forecasts: list[KMAForecast] = []
        for (fcst_date, fcst_time), cats in sorted(grouped.items()):
            forecast_for = datetime.strptime(
                f"{fcst_date}{fcst_time}", "%Y%m%d%H%M"
            ).replace(tzinfo=KST)
            forecasts.append(
                KMAForecast(
                    forecast_for=forecast_for,
                    temperature_c=_parse_float(cats.get("T1H")),
                    humidity_pct=_parse_float(cats.get("REH")),
                    wind_speed_ms=_parse_float(cats.get("WSD")),
                    wind_direction_deg=(
                        (_parse_float(cats.get("VEC")) or 0.0) % 360.0
                        if cats.get("VEC") else None
                    ),
                    precipitation_mm=_parse_float(cats.get("RN1")),
                    sky_condition=SKY_CODE.get(cats.get("SKY", "")),
                    precipitation_type=PTY_CODE.get(cats.get("PTY", "")),
                )
            )
        return forecasts

    # ===== 단기예보 (앞으로 3일, 3시간 단위) =====

    def _get_vilage_fcst_base_time(self) -> tuple[str, str]:
        """단기예보 기준시각 — 하루 8번 발표 (02,05,08,11,14,17,20,23시)."""
        now = datetime.now(KST)
        # 발표시각 10분 이전이면 이전 발표 사용
        hours_available = [2, 5, 8, 11, 14, 17, 20, 23]
        current_hour = now.hour

        # 오늘 발표된 것 중 "현재 시각 - 10분" 이전에 발표된 가장 최근 것
        check_time = now - timedelta(minutes=10)
        target_hour = None
        for h in reversed(hours_available):
            if h <= check_time.hour:
                target_hour = h
                break

        if target_hour is None:
            # 오늘 아직 발표 안 됨 → 어제 23시
            base_dt = (now - timedelta(days=1)).replace(hour=23)
        else:
            base_dt = now.replace(hour=target_hour)

        return base_dt.strftime("%Y%m%d"), f"{base_dt.hour:02d}00"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def get_short_term_forecast(
        self, lat: float, lon: float
    ) -> list[KMAForecast]:
        """앞으로 3일 이내 예보 (3시간 단위, 최대 72시간)."""
        grid = latlon_to_grid(lat, lon)
        base_date, base_time = self._get_vilage_fcst_base_time()

        params = {
            "authKey": self.api_key,
            "numOfRows": "1000",  # 3일치 전체
            "pageNo": "1",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

        url = f"{self.base_url}/VilageFcstInfoService_2.0/getVilageFcst"
        response = await self._client.get(url, params=params)
        response.raise_for_status()

        try:
            data = response.json()
            items = data["response"]["body"]["items"]["item"]
        except (KeyError, ValueError) as e:
            raise KMAError(
                f"KMA vilageFcst parse failed: {response.text[:200]}"
            ) from e

        grouped: dict[tuple[str, str], dict[str, str]] = {}
        for item in items:
            key = (item["fcstDate"], item["fcstTime"])
            grouped.setdefault(key, {})[item["category"]] = item["fcstValue"]

        forecasts: list[KMAForecast] = []
        for (fcst_date, fcst_time), cats in sorted(grouped.items()):
            forecast_for = datetime.strptime(
                f"{fcst_date}{fcst_time}", "%Y%m%d%H%M"
            ).replace(tzinfo=KST)
            # 단기예보는 TMP가 기온 (T1H 아님)
            temp = cats.get("TMP")
            forecasts.append(
                KMAForecast(
                    forecast_for=forecast_for,
                    temperature_c=_parse_float(temp),
                    humidity_pct=_parse_float(cats.get("REH")),
                    wind_speed_ms=_parse_float(cats.get("WSD")),
                    wind_direction_deg=(
                        (_parse_float(cats.get("VEC")) or 0.0) % 360.0
                        if cats.get("VEC") else None
                    ),
                    precipitation_mm=_parse_float(cats.get("PCP")),
                    sky_condition=SKY_CODE.get(cats.get("SKY", "")),
                    precipitation_type=PTY_CODE.get(cats.get("PTY", "")),
                )
            )
        return forecasts

    # ===== 편의 메서드: 특정 미래 시각의 예보 찾기 =====

    async def get_forecast_at(
        self, lat: float, lon: float, target_time: datetime
    ) -> KMAForecast | None:
        """지정 시각에 가장 가까운 예보 반환.

        6시간 이내면 초단기예보, 그 이후면 단기예보 사용.
        """
        now = datetime.now(KST)
        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=KST)

        delta_hours = (target_time - now).total_seconds() / 3600

        if delta_hours <= 6:
            forecasts = await self.get_ultra_short_forecast(lat, lon)
        else:
            forecasts = await self.get_short_term_forecast(lat, lon)

        if not forecasts:
            return None

        # 가장 가까운 시각 찾기
        return min(
            forecasts,
            key=lambda f: abs((f.forecast_for - target_time).total_seconds()),
        )
