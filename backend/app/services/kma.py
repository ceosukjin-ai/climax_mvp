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
        base_url: str = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0",
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
            "serviceKey": self.api_key,
            "numOfRows": "100",
            "pageNo": "1",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

        url = f"{self.base_url}/getUltraSrtNcst"
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
            "serviceKey": self.api_key,
            "numOfRows": "200",
            "pageNo": "1",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

        url = f"{self.base_url}/getUltraSrtFcst"
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
            "serviceKey": self.api_key,
            "numOfRows": "1000",  # 3일치 전체
            "pageNo": "1",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(grid.nx),
            "ny": str(grid.ny),
        }

        url = f"{self.base_url}/getVilageFcst"
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


# =============================================================================
# ASOS 실시간 지상관측 — 기상청 API허브 (2026-08-11)
# =============================================================================
# 동네예보 격자에는 없는 실측 항목(일사·전운량·지면온도)을 관측소 단위로 받는다.
#   · 일사 SI [MJ/m², 1시간 누적] → 추정 GHI 보정(운량 역산)
#   · 전운량 CA_TOT [0~10]        → 예보 SKY 코드보다 정확한 운량
#   · 지면온도 TS [°C]            → 엔진 Tsurf(에너지수지) 교정 로그
# 인증키는 apihub.kma.go.kr 회원키(authKey) — 공공데이터포털 키와 별개.
# 호출량: 관측소당 시간 1회 수준(오케스트레이터에서 캐시) — 한도 무관.

APIHUB_SFCTM2_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"

# 관측소 위경도 — 운량은 국지성이 있어 가까운 관측소만 신뢰한다(반경 50km).
#
# 2026-08-26 전국 확장. 그 전까지 부산(159) 한 곳뿐이라 서울·대구 사용자는 실측
# 운량을 한 번도 못 받고 항상 SKY 예보로 폴백하고 있었다. 앱은 전국 개방인데
# 실측 축은 부산에만 있었던 셈이다.
#
# ✅ 아래 값은 **기상청 API허브 지점정보(stn_inf, inf=SFC)에서 받아 대조한 것**이다.
#    추정이 아니라 원본에서 온 값이다. 손으로 적어 넣었던 초안에서 232 천안이
#    15.4km 어긋나 있었고, 그 검증으로 잡았다.
#
#    표를 고칠 때는 반드시 다시 대조할 것:
#        cd ~/climax_mvp
#        set -a; . infra/ncp/.env.prod; set +a
#        python3 scripts/verify_asos_stations.py
#
# 관측소가 50km 안에 없으면 nearest_station 이 None 을 돌려주고 SKY 예보로
# 폴백한다. 그래서 표가 불완전해도 서비스는 안 죽는다 — 정확도만 떨어진다.
#
# ⚠️ 산지 관측소(대관령 772m, 태백 714m)는 주변 저지대와 운량·기온이 크게 다르다.
#    거리로만 고르므로 그 일대 사용자에게는 산 위 값이 갈 수 있다. 실측이 예보보다
#    나쁠 수 있는 유일한 경우다 — 실사용 데이터가 쌓이면 재검토할 것.
ASOS_STATIONS: dict[int, tuple[float, float]] = {
    90: (38.25085, 128.56473),      # 속초
    93: (37.94738, 127.75443),      # 북춘천
    95: (38.14787, 127.30420),      # 철원
    98: (37.90188, 127.06070),      # 동두천
    99: (37.88589, 126.76648),      # 파주
    100: (37.67713, 128.71834),     # 대관령(산지 772m)
    101: (37.90262, 127.73570),     # 춘천
    102: (37.97396, 124.71237),     # 백령도
    104: (37.80456, 128.85535),     # 북강릉
    105: (37.75147, 128.89099),     # 강릉
    106: (37.50709, 129.12433),     # 동해
    108: (37.57142, 126.96580),     # 서울
    112: (37.47772, 126.62490),     # 인천
    114: (37.33749, 127.94659),     # 원주
    115: (37.48129, 130.89863),     # 울릉도
    119: (37.25746, 126.98300),     # 수원
    121: (37.18126, 128.45743),     # 영월
    127: (36.97045, 127.95250),     # 충주
    129: (36.77658, 126.49390),     # 서산
    130: (36.99176, 129.41278),     # 울진
    131: (36.63924, 127.44066),     # 청주
    133: (36.37199, 127.37210),     # 대전
    135: (36.22025, 127.99458),     # 추풍령
    136: (36.57293, 128.70733),     # 안동
    137: (36.40837, 128.15741),     # 상주
    138: (36.03201, 129.38002),     # 포항
    140: (36.00530, 126.76135),     # 군산
    143: (35.87797, 128.65296),     # 대구
    146: (35.84092, 127.11718),     # 전주
    152: (35.58237, 129.33469),     # 울산
    155: (35.17019, 128.57282),     # 창원
    156: (35.17294, 126.89156),     # 광주
    159: (35.10468, 129.03203),     # 부산
    162: (34.84541, 128.43561),     # 통영
    165: (34.81732, 126.38151),     # 목포
    168: (34.73929, 127.74063),     # 여수
    169: (34.68719, 125.45105),     # 흑산도
    170: (34.39590, 126.70182),     # 완도
    172: (35.34824, 126.59900),     # 고창
    174: (35.02040, 127.36940),     # 순천
    177: (36.65759, 126.68772),     # 홍성
    181: (36.63972, 127.39694),     # 서청주
    184: (33.51411, 126.52969),     # 제주
    185: (33.29382, 126.16283),     # 고산
    188: (33.38677, 126.88020),     # 성산
    189: (33.24616, 126.56530),     # 서귀포
    192: (35.16378, 128.04004),     # 진주
    201: (37.70739, 126.44634),     # 강화
    202: (37.48863, 127.49446),     # 양평
    203: (37.26399, 127.48421),     # 이천
    211: (38.05986, 128.16714),     # 인제
    212: (37.68360, 127.88043),     # 홍천
    216: (37.17038, 128.98929),     # 태백(산지 714m)
    217: (37.38071, 128.67312),     # 정선군
    221: (37.15928, 128.19433),     # 제천
    226: (36.48761, 127.73415),     # 보은
    232: (36.76217, 127.29282),     # 천안
    235: (36.32724, 126.55744),     # 보령
    236: (36.27242, 126.92079),     # 부여
    238: (36.10563, 127.48175),     # 금산
    239: (36.48522, 127.24438),     # 세종
    243: (35.72961, 126.71657),     # 부안
    244: (35.61203, 127.28556),     # 임실
    245: (35.56333, 126.83904),     # 정읍
    247: (35.42130, 127.39652),     # 남원
    248: (35.65696, 127.52031),     # 장수
    251: (35.42661, 126.69700),     # 고창군
    252: (35.28366, 126.47784),     # 영광군
    253: (35.22981, 128.89075),     # 김해시
    254: (35.37131, 127.12860),     # 순창군
    255: (35.22655, 128.67260),     # 북창원
    257: (35.30737, 129.02009),     # 양산시
    258: (34.76335, 127.21226),     # 보성군
    259: (34.64457, 126.78408),     # 강진군
    260: (34.68886, 126.91951),     # 장흥
    261: (34.55375, 126.56907),     # 해남
    262: (34.61826, 127.27572),     # 고흥
    263: (35.32258, 128.28812),     # 의령군
    264: (35.51138, 127.74538),     # 함양군
    266: (34.94340, 127.69140),     # 광양시
    268: (34.47296, 126.25846),     # 진도군
    271: (36.94361, 128.91449),     # 봉화
    272: (36.87183, 128.51687),     # 영주
    273: (36.62727, 128.14879),     # 문경
    276: (36.43510, 129.04005),     # 청송군
    277: (36.53337, 129.40926),     # 영덕
    278: (36.35610, 128.68864),     # 의성
    279: (36.13055, 128.32055),     # 구미
    281: (35.97742, 128.95140),     # 영천
    283: (35.81747, 129.20123),     # 경주시
    284: (35.66739, 127.90990),     # 거창
    285: (35.56505, 128.16994),     # 합천
    288: (35.49147, 128.74412),     # 밀양
    289: (35.41300, 127.87910),     # 산청
    294: (34.88818, 128.60459),     # 거제
    295: (34.81662, 127.92641),     # 남해
    296: (35.21778, 128.96024),     # 북부산
}

# kma_sfctm2 고정 컬럼(공백 구분, 0-based) — help=1 문서 기준
_SFC_IDX_TM = 0
_SFC_IDX_STN = 1
_SFC_IDX_WS = 3
_SFC_IDX_TA = 11
_SFC_IDX_HM = 13
_SFC_IDX_CA_TOT = 25
_SFC_IDX_SI = 34
_SFC_IDX_TS = 36
_SFC_MIN_TOKENS = 37


@dataclass(frozen=True, slots=True)
class ASOSObservation:
    """API허브 지상관측(종관) 시간자료 1건 — 결측은 None."""

    station_id: int
    observed_at: datetime            # 관측시각(KST) — SI는 이 시각까지 1시간 누적
    temperature_c: float | None
    humidity_pct: float | None
    wind_speed_ms: float | None
    cloud_cover_tenths: float | None  # 전운량 [0~10]
    solar_mj: float | None            # 1시간 일사 [MJ/m²]
    ground_temp_c: float | None       # 지면온도 [°C]

    @property
    def solar_avg_wm2(self) -> float | None:
        """1시간 누적 일사 → 시간평균 일사 [W/m²] (MJ/m²·h × 10⁶/3600)."""
        if self.solar_mj is None:
            return None
        return self.solar_mj * (1_000_000.0 / 3600.0)


def _sfc_value(tokens: list[str], idx: int, lo: float, hi: float) -> float | None:
    """토큰 → float. 결측 코드(-9/-99 계열)·범위 밖은 None."""
    try:
        v = float(tokens[idx])
    except (IndexError, ValueError):
        return None
    if v in (-9.0, -99.0, -999.0, -9.9):
        return None
    if not (lo <= v <= hi):
        return None
    return v


def parse_sfctm2(text: str, station_id: int) -> ASOSObservation | None:
    """kma_sfctm2 응답(텍스트)에서 지정 관측소의 최신 1건 파싱.

    형식: '#' 주석 헤더 + 공백 구분 고정 컬럼 데이터 행.
    컬럼 어긋남(문서와 다른 버전) 방어: TM 형식·STN 일치·기온 범위 검증.
    """
    best: ASOSObservation | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("="):
            continue
        tokens = line.split()
        if len(tokens) < _SFC_MIN_TOKENS:
            continue
        tm, stn = tokens[_SFC_IDX_TM], tokens[_SFC_IDX_STN]
        if len(tm) != 12 or not tm.isdigit():
            continue
        if stn != str(station_id):
            continue
        observed_at = datetime.strptime(tm, "%Y%m%d%H%M").replace(tzinfo=KST)
        obs = ASOSObservation(
            station_id=station_id,
            observed_at=observed_at,
            temperature_c=_sfc_value(tokens, _SFC_IDX_TA, -45.0, 55.0),
            humidity_pct=_sfc_value(tokens, _SFC_IDX_HM, 0.0, 100.0),
            wind_speed_ms=_sfc_value(tokens, _SFC_IDX_WS, 0.0, 80.0),
            cloud_cover_tenths=_sfc_value(tokens, _SFC_IDX_CA_TOT, 0.0, 10.0),
            solar_mj=_sfc_value(tokens, _SFC_IDX_SI, 0.0, 5.0),
            ground_temp_c=_sfc_value(tokens, _SFC_IDX_TS, -45.0, 80.0),
        )
        # 최신 시각 우선
        if best is None or obs.observed_at > best.observed_at:
            best = obs
    return best


class ASOSClient:
    """기상청 API허브 지상관측(종관) 시간자료 비동기 클라이언트."""

    def __init__(self, auth_key: str, timeout_sec: float = 10.0) -> None:
        if not auth_key:
            raise ValueError("API허브 인증키(authKey)가 필요합니다")
        self.auth_key = auth_key
        self._client = httpx.AsyncClient(timeout=timeout_sec)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def nearest_station(
        lat: float, lon: float, max_km: float = 50.0
    ) -> int | None:
        """좌표에서 max_km 이내 최근접 관측소 ID. 없으면 None(→ SKY 예보 폴백)."""
        best_id, best_km = None, max_km
        for stn, (slat, slon) in ASOS_STATIONS.items():
            dlat = math.radians(slat - lat)
            dlon = math.radians(slon - lon)
            a = (
                math.sin(dlat / 2) ** 2
                + math.cos(math.radians(lat))
                * math.cos(math.radians(slat))
                * math.sin(dlon / 2) ** 2
            )
            km = 2 * 6371.0 * math.asin(math.sqrt(a))
            if km <= best_km:
                best_id, best_km = stn, km
        return best_id

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def get_hourly(self, station_id: int = 159) -> ASOSObservation | None:
        """최신 정시 관측 1건. 발표 지연(~10여 분)을 고려해 현재 정시→직전 정시 순 시도."""
        now = datetime.now(KST)
        candidates = [
            (now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=h))
            for h in (0, 1, 2)
        ]
        for tm in candidates:
            params = {
                "tm": tm.strftime("%Y%m%d%H%M"),
                "stn": str(station_id),
                "help": "0",
                "authKey": self.auth_key,
            }
            resp = await self._client.get(APIHUB_SFCTM2_URL, params=params)
            resp.raise_for_status()
            text = resp.content.decode("euc-kr", errors="replace")
            if '"status"' in text and "403" in text:
                raise KMAError("API허브 활용신청 필요 또는 인증키 오류 (403)")
            obs = parse_sfctm2(text, station_id)
            if obs is not None:
                return obs
        return None
