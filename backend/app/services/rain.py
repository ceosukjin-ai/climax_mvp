"""좌표 단위 강수 정보 (2026-09-01).

기존 get_precipitation_outlook 은 "최근접 격자 하나"의 초단기예보를 그대로 썼다.
동네예보 격자는 5km라서 광안리와 연산동이 같은 값을 볼 수 있었다. 좌표를 정확히
알면서 격자 값을 옮겨 쓰고 있던 셈이다.

이 모듈이 하는 일은 세 가지다.

  ① 격자 보간 — 인접 4격자를 거리 가중으로 섞어 좌표 단위 값을 만든다.
  ② 사실 우선  — "지금 비가 오나"는 예보가 아니라 초단기실황(관측 분석장)과
                 주변 관측소 실측으로 답한다.
  ③ 접근 판정 — 바람 불어오는 쪽 관측소에 비가 오고 있으면 도착시간을 추정한다.
                 레이더 없이, 관측 사실만으로.

하지 않는 것
------------
* 기상청 예보를 대체하지 않는다. 정확도의 천장은 원자료다.
* 특보를 대신하지 않는다. 호우·태풍은 기상청 특보를 그대로 노출해야 한다.
* ③의 도착시간은 **추정**이다. 단일 시각으로 단정하지 않고 범위와 신뢰도를 함께 준다.
  지상풍으로 강수대 이동을 근사하므로 실제와 어긋날 수 있다. 관측소가 없는 방향은
  아예 모른다 — 모르는 것은 모른다고 말한다.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.services.aws_obs import StationRain
from app.services.kma import (
    ASOS_STATION_NAMES,
    ASOS_STATIONS,
    KMAClient,
    bearing_deg,
    compass_name,
    grid_to_latlon,
    haversine_km,
    latlon_to_grid_float,
)

KST = timezone(timedelta(hours=9))

# --- 튜닝 상수 (전부 근거와 함께 적는다) ---

GRID_WEIGHT_FLOOR = 0.05      # 이보다 작은 가중치의 격자는 호출하지 않는다(호출 절약)
NCST_TTL_SEC = 10 * 60        # 초단기실황은 매시 발표 — 10분 캐시
FCST_TTL_SEC = 15 * 60        # 초단기예보는 매시 30분 발표 — 15분 캐시
OBS_TTL_SEC = 10 * 60         # 전 지점 관측 훑기 — 10분 캐시
PREWARM_INTERVAL_SEC = 5 * 60  # 미리 받아두는 주기 (캐시 만료보다 짧게)
FAIL_TTL_SEC = 3 * 60         # 실패 시 잠시 재시도 억제

NEARBY_RADIUS_KM = 120.0      # 주변 관측소 탐색 반경 (표시용)
# 접근 판정은 더 좁게 본다. 120km 밖의 비를 "다가온다"고 하면
# 실제로는 몇 시간이 걸리고 오는 중에 사라지는 일이 흔하다.
# (2026-09-01 운영 확인: 119.8km 밖 비 때문에 우산을 권하고 있었다)
APPROACH_MAX_KM = 60.0
HERE_RADIUS_KM = 8.0          # 이 안의 관측소 강수를 "여기"로 본다.
# 15km 였는데 2026-09-01 검증에서 13.9km 떨어진 지점의 빗방울을 "여기 비"로
# 단정하는 문제가 드러났다. 5km 격자가 뭉뚱그린다고 말해놓고 15km 를 여기라 하면
# 앞뒤가 안 맞는다. AWS 로 지점이 촘촘해져(반경 8km 안 3~5곳) 좁힐 수 있게 됐다.
HERE_CONSENSUS = 0.5          # 반경 안 지점 중 이 비율 이상이 비여야 '여기 비'
UPWIND_TOLERANCE_DEG = 60.0   # 풍향 ±이 각도 안이면 '바람 불어오는 쪽'으로 본다

# 강수대 이동속도 ÷ 지상풍속. 지상풍은 마찰로 상층 흐름보다 느려서 그대로 쓰면
# 도착시간을 과대평가한다. 문헌으로 확정된 값이 아니라 공학적 근사이므로
# 범위를 준 뒤 사용자에게도 범위로 보여준다.
STEERING_RATIO_MIN = 1.5
STEERING_RATIO_MAX = 2.5
SYSTEM_SPEED_MIN_KMH = 8.0
SYSTEM_SPEED_MAX_KMH = 90.0

# 바람이 이보다 약하면 이동 방향·속도 자체가 불확실하다. 시간은 말해주되
# 그 사실을 함께 말한다 — 숫자만 던지면 사용자는 그게 확정인 줄 안다.
WEAK_WIND_MS = 2.0
# 이보다 멀면 분 단위로 말하는 것이 의미가 없다
ETA_HORIZON_MIN = 180

RAIN_PTY = {"비", "비/눈", "눈", "소나기", "빗방울", "빗방울눈날림", "눈날림"}

# 예보-관측 대조: 이 시간 안에 비 예보인데 주변에 비가 하나도 없으면 어긋남으로 본다.
MISMATCH_HORIZON_H = 3.0
MISMATCH_IMMINENT_H = 1.0

# 자료 신선도. 초단기실황은 정시 자료가 10~40분 뒤에 나온다. 낡은 값으로
# "지금 비가 옵니다"라고 말하면, 이미 그친 비를 현재로 보고하는 셈이 된다.
STALE_MIN = 45          # 이보다 오래된 자료면 '지금'이라고 단정하지 않는다
VERY_STALE_MIN = 90     # 이보다 오래되면 신뢰도를 낮음으로


# ---------------------------------------------------------------- 격자 보간

@dataclass(frozen=True, slots=True)
class GridWeight:
    nx: int
    ny: int
    weight: float


def grid_weights(lat: float, lon: float) -> list[GridWeight]:
    """좌표를 둘러싼 격자 4개와 이중선형 가중치.

    격자 번호는 소수 격자좌표를 반올림한 값이므로, 정수 좌표가 곧 격자 중심이다.
    따라서 floor/ceil 두 칸 사이를 선형 보간하면 된다.

    가중치가 아주 작은 격자는 버리고 나머지를 재정규화한다 — 화면에 안 보일 차이를
    위해 API 를 네 번 부를 이유가 없다. 사용자가 격자 중심 근처면 호출은 1회로 끝난다.
    """
    fx, fy = latlon_to_grid_float(lat, lon)
    x0, y0 = math.floor(fx), math.floor(fy)
    tx, ty = fx - x0, fy - y0

    raw = [
        (x0, y0, (1 - tx) * (1 - ty)),
        (x0 + 1, y0, tx * (1 - ty)),
        (x0, y0 + 1, (1 - tx) * ty),
        (x0 + 1, y0 + 1, tx * ty),
    ]
    kept = [(nx, ny, w) for nx, ny, w in raw if w >= GRID_WEIGHT_FLOOR]
    if not kept:                                  # 이론상 불가하지만 방어
        kept = [max(raw, key=lambda r: r[2])]
    total = sum(w for _, _, w in kept)
    return [GridWeight(nx, ny, w / total) for nx, ny, w in kept]


def _wmean(pairs: list[tuple[float | None, float]]) -> float | None:
    """(값, 가중치) 목록의 가중평균. 값이 None 인 항목은 빼고 재정규화."""
    ok = [(v, w) for v, w in pairs if v is not None]
    if not ok:
        return None
    tw = sum(w for _, w in ok)
    if tw <= 0:
        return None
    return sum(v * w for v, w in ok) / tw


# ---------------------------------------------------------------- 결과 구조

@dataclass
class RainHour:
    at: datetime
    in_hours: int
    precip_mm: float
    pty: str | None
    sky: str | None

    @property
    def is_rain(self) -> bool:
        return (self.pty in RAIN_PTY) or self.precip_mm > 0.0


@dataclass(frozen=True, slots=True)
class LocalVerdict:
    """반경 안 지점들의 합의. 한 곳만 보고 '여기 비' 라고 하지 않기 위한 것."""

    known: int = 0
    rain: int = 0
    drizzle: int = 0
    aloft_known: int = 0     # 레이더가 값을 준 지점
    aloft: int = 0           # 그중 하늘에 비가 있는 지점

    @property
    def level(self) -> str | None:
        """비 / 빗방울 / 없음 / None(반경 안에 판정 가능한 지점이 없음)."""
        if self.known == 0:
            return None
        if self.known == 1:
            # 한 곳뿐이면 그 곳을 따르되, 신뢰도는 호출부에서 낮춘다
            return "비" if self.rain else ("빗방울" if self.drizzle else "없음")
        if self.rain / self.known >= HERE_CONSENSUS:
            return "비"
        if (self.rain + self.drizzle) / self.known >= HERE_CONSENSUS:
            return "빗방울"
        # 일부만 비 → 여기 비가 아니라 근처에 비
        return "없음"

    @property
    def lone(self) -> bool:
        return self.known == 1

    @property
    def sky_only(self) -> bool:
        """땅엔 안 오는데 하늘엔 비가 있다 — 증발·꼬리구름 상황."""
        if self.aloft_known == 0 or self.known == 0:
            return False
        ground_wet = (self.rain + self.drizzle) / self.known >= HERE_CONSENSUS
        return (self.aloft / self.aloft_known) >= HERE_CONSENSUS and not ground_wet


@dataclass
class NearbyStation:
    stn: int
    name: str
    km: float
    bearing: float
    direction: str
    rn_mm: float
    upwind: bool
    level: str | None = None        # 비 / 빗방울 / 상공(레이더에만)
    radar_mmh: float | None = None  # 레이더 강우강도
    echo_height_m: float | None = None
    evidence: str | None = None     # 현천 / 강수감지 / 우량계 — 무엇으로 판정했나
    minutes: float | None = None    # RE_SUM — 최근 1시간 중 비가 감지된 분


@dataclass
class RainAt:
    lat: float
    lon: float
    raining_now: bool
    now_precip_mm: float
    now_source: str
    grid_blend: list[dict] = field(default_factory=list)
    timeline: list[RainHour] = field(default_factory=list)
    onset_at: datetime | None = None
    clearing_at: datetime | None = None
    umbrella_window: str | None = None
    nearest_rain: NearbyStation | None = None
    approaching: NearbyStation | None = None
    eta_min_range: tuple[int, int] | None = None
    stations_raining: int = 0
    obs_available: bool = False     # 관측 훑기가 실제로 성공했는가
    obs_kind: str = "없음"           # 무엇으로 판정했나 (AWS / ASOS)
    level: str = "없음"              # 여기 상태 — 비 / 빗방울 / 없음
    local_known: int = 0             # 반경 안 판정 가능 지점 수
    local_rain: int = 0
    local_drizzle: int = 0
    local_aloft: int = 0
    local_aloft_known: int = 0
    sky_only: bool = False           # 하늘엔 있는데 땅엔 안 닿는 상태
    radar_nationwide: int = 0        # 전국에서 레이더 값이 온 지점 수
    wind_ms: float | None = None
    data_at: datetime | None = None  # '지금' 값의 기준 시각 (가장 신선한 자료)
    data_age_min: int | None = None  # 그 자료가 몇 분 전 것인가
    stale: bool = False              # 낡은 자료로 '지금'을 단정하면 안 되는 상태
    mismatch: str | None = None     # "예보만" | "관측만" | "일치" | None
    confidence: str = "낮음"
    advice: str = ""
    updated_at: datetime | None = None


# ---------------------------------------------------------------- 서비스

class RainService:
    """좌표 단위 강수 판정. KMAClient·ASOSClient 를 주입받는다(테스트 용이)."""

    def __init__(self, kma: KMAClient, asos=None, aws=None) -> None:
        self.kma = kma
        self.asos = asos
        self.aws = aws          # app.services.aws_obs.AWSObsClient (선택)
        self._ncst: dict[tuple[int, int], tuple[object, float]] = {}
        self._fcst: dict[tuple[int, int], tuple[object, float]] = {}
        self._obs: tuple[tuple | None, float] = (None, 0.0)
        self.obs_kind: str = "없음"     # 이번 판정에 무엇을 썼나 (AWS / ASOS / 없음)
        self._prewarm_task = None
        self.radar_nationwide: int = 0   # 전국에서 레이더 값이 온 지점 수 (진단용)

    # --- 미리 받아두기 ---------------------------------------------------
    #
    # 전 지점 관측은 좌표와 무관한 **전국 공통 자료**다. 사용자 요청이 들어온 뒤에
    # 받으면 첫 사람이 1분을 기다린다(2026-09-01 운영 확인). 서버가 주기적으로
    # 미리 받아 캐시에 넣어두면 요청은 캐시만 읽는다.

    async def _prewarm_loop(self, interval_sec: int) -> None:
        import asyncio
        while True:
            try:
                recs, _, _, kind = await self._station_data()
                radar = sum(1 for v in recs.values() if v.radar_mmh is not None)
                wet = sum(1 for v in recs.values() if v.intensity in ("비", "빗방울"))
                logger.info(
                    "강수 관측 미리 받기 완료 — {} {}곳 (레이더 {}곳 · 강수 {}곳)",
                    kind, len(recs), radar, wet)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("강수 관측 미리 받기 실패: {}", e)
            await asyncio.sleep(interval_sec)

    def start_prewarm(self, interval_sec: int = PREWARM_INTERVAL_SEC) -> None:
        import asyncio
        if self._prewarm_task is not None:
            return
        self._prewarm_task = asyncio.create_task(self._prewarm_loop(interval_sec))
        logger.info("강수 관측 미리 받기 시작 ({}초 주기)", interval_sec)

    async def stop_prewarm(self) -> None:
        import asyncio
        if self._prewarm_task is None:
            return
        self._prewarm_task.cancel()
        try:
            await self._prewarm_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._prewarm_task = None
        self.radar_nationwide: int = 0   # 전국에서 레이더 값이 온 지점 수 (진단용)

    # --- 캐시된 격자 조회 -------------------------------------------------

    async def _ncst_at_grid(self, gw: GridWeight):
        key = (gw.nx, gw.ny)
        hit = self._ncst.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        lat, lon = grid_to_latlon(gw.nx, gw.ny)
        try:
            obs = await self.kma.get_current_observation(lat, lon)
            ttl = NCST_TTL_SEC
        except Exception as e:  # noqa: BLE001
            logger.warning("초단기실황 실패 nx={} ny={}: {}", gw.nx, gw.ny, e)
            obs, ttl = None, FAIL_TTL_SEC
        self._ncst[key] = (obs, time.monotonic() + ttl)
        return obs

    async def _fcst_at_grid(self, gw: GridWeight):
        key = (gw.nx, gw.ny)
        hit = self._fcst.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        lat, lon = grid_to_latlon(gw.nx, gw.ny)
        try:
            fc = await self.kma.get_ultra_short_forecast(lat, lon)
            ttl = FCST_TTL_SEC
        except Exception as e:  # noqa: BLE001
            logger.warning("초단기예보 실패 nx={} ny={}: {}", gw.nx, gw.ny, e)
            fc, ttl = None, FAIL_TTL_SEC
        self._fcst[key] = (fc, time.monotonic() + ttl)
        return fc

    async def _station_data(self):
        """지점별 관측과 지점 좌표표를 함께 돌려준다.

        AWS(500여 곳, 현천·강수감지 포함)를 우선하고, 못 쓰면 ASOS(97곳)로 물러난다.
        AWS 가 없다고 기능이 죽지는 않는다 — 정밀도만 떨어진다.
        """
        cached, exp = self._obs
        if cached is not None and exp > time.monotonic():
            return cached

        result = None
        if self.aws is not None:
            try:
                if await self.aws.ensure_stations():
                    recs = await self.aws.observe()
                    if recs:
                        result = (recs, self.aws.registry.stations,
                                  self.aws.registry.name, "AWS")
            except Exception as e:  # noqa: BLE001
                logger.warning("AWS 관측 조회 실패 — ASOS 로 폴백: {}", e)

        if result is None and self.asos is not None:
            try:
                rows = await self.asos.get_all_hourly()
                recs = {}
                for stn, r in rows.items():
                    tm = str(r.get("tm", ""))
                    at = (datetime.strptime(tm, "%Y%m%d%H%M").replace(tzinfo=KST)
                          if len(tm) == 12 and tm.isdigit() else None)
                    recs[stn] = StationRain(stn=stn, tm=at, rn_mm=r.get("rn_mm"))
                if recs:
                    result = (recs, ASOS_STATIONS,
                              lambda s: ASOS_STATION_NAMES.get(s, str(s)), "ASOS")
            except Exception as e:  # noqa: BLE001
                logger.warning("ASOS 관측 조회 실패: {}", e)

        if result is None:
            result = ({}, {}, lambda s: str(s), "없음")

        ttl = OBS_TTL_SEC if result[0] else FAIL_TTL_SEC
        self._obs = (result, time.monotonic() + ttl)
        return result

    # --- ① 현재 상태 (격자 보간) -----------------------------------------

    async def _now(
        self, weights: list[GridWeight]
    ) -> tuple[float | None, float | None, float | None, datetime | None]:
        """보간된 (강수량 mm, 풍속 m/s, 풍향 deg, 관측시각)."""
        precip, wsd, vec_sin, vec_cos = [], [], [], []
        observed_at: datetime | None = None
        for gw in weights:
            obs = await self._ncst_at_grid(gw)
            if obs is None:
                continue
            # 격자마다 발표시각이 같지만, 다르면 가장 오래된 쪽을 자료 나이로 본다
            if observed_at is None or obs.observed_at < observed_at:
                observed_at = obs.observed_at
            precip.append((obs.precipitation_mm, gw.weight))
            wsd.append((obs.wind_speed_ms, gw.weight))
            # 풍향은 각도라 산술평균이 안 된다 — 벡터로 평균낸다
            rad = math.radians(obs.wind_direction_deg)
            vec_sin.append((math.sin(rad), gw.weight))
            vec_cos.append((math.cos(rad), gw.weight))

        s, c = _wmean(vec_sin), _wmean(vec_cos)
        wd = (math.degrees(math.atan2(s, c)) + 360.0) % 360.0 if (s is not None and c is not None) else None
        return _wmean(precip), _wmean(wsd), wd, observed_at

    # --- ② 0~6시간 (격자 보간) -------------------------------------------

    async def _timeline(self, weights: list[GridWeight]) -> list[RainHour]:
        now = datetime.now(KST)
        buckets: dict[datetime, dict] = {}
        for gw in weights:
            fc = await self._fcst_at_grid(gw)
            if not fc:
                continue
            for f in fc:
                b = buckets.setdefault(
                    f.forecast_for, {"mm": [], "pty": {}, "sky": {}}
                )
                b["mm"].append((f.precipitation_mm, gw.weight))
                if f.precipitation_type:
                    b["pty"][f.precipitation_type] = b["pty"].get(f.precipitation_type, 0.0) + gw.weight
                if f.sky_condition:
                    b["sky"][f.sky_condition] = b["sky"].get(f.sky_condition, 0.0) + gw.weight

        out: list[RainHour] = []
        for at in sorted(buckets)[:6]:
            b = buckets[at]
            # 강수형태·하늘상태는 숫자가 아니라 범주 → 가중치 합이 가장 큰 쪽을 고른다.
            # 다만 '없음'과 '비'가 갈리면 비 쪽을 살린다(놓치는 것보다 낫다).
            pty = max(b["pty"], key=b["pty"].get) if b["pty"] else None
            rain_w = sum(w for k, w in b["pty"].items() if k in RAIN_PTY)
            if rain_w >= 0.35 and pty not in RAIN_PTY:
                pty = max((k for k in b["pty"] if k in RAIN_PTY),
                          key=b["pty"].get, default=pty)
            out.append(RainHour(
                at=at,
                in_hours=max(0, round((at - now).total_seconds() / 3600)),
                precip_mm=round(_wmean(b["mm"]) or 0.0, 1),
                pty=pty,
                sky=max(b["sky"], key=b["sky"].get) if b["sky"] else None,
            ))
        return out

    # --- ③ 주변 관측소 --------------------------------------------------

    async def _nearby(
        self, lat: float, lon: float, wind_dir: float | None
    ) -> tuple[list[NearbyStation], int, bool, datetime | None, LocalVerdict]:
        recs, coords, name_of, kind = await self._station_data()
        self.obs_kind = kind

        found: list[NearbyStation] = []
        raining = 0
        known = 0                 # 온다/안온다를 실제로 판정할 수 있었던 지점 수
        here_known = here_rain = here_drizzle = 0    # 반경 HERE_RADIUS_KM 안 집계
        here_aloft_known = here_aloft = 0            # 하늘(레이더) 집계
        radar_nationwide = 0                         # 전국에서 레이더 값이 온 지점
        obs_at: datetime | None = None

        for stn, rec in recs.items():
            if rec.tm is not None and (obs_at is None or rec.tm > obs_at):
                obs_at = rec.tm

            level = rec.intensity        # 비 / 빗방울 / 없음 / None(모른다)
            sky = rec.aloft              # True / False / None(레이더 반경 밖)
            if rec.radar_mmh is not None:
                radar_nationwide += 1
            if level is None and sky is None:
                continue
            if level is not None:
                known += 1

            pos = coords.get(stn)
            if pos is None:
                if level not in (None, "없음"):
                    raining += 1
                continue
            slat, slon = pos
            km = haversine_km(lat, lon, slat, slon)

            # 여기 집계 — 한 곳만 보고 정하지 않는다
            if km <= HERE_RADIUS_KM:
                if level is not None:
                    here_known += 1
                    if level == "비":
                        here_rain += 1
                    elif level == "빗방울":
                        here_drizzle += 1
                if sky is not None:
                    here_aloft_known += 1
                    if sky:
                        here_aloft += 1

            # 땅에도 하늘에도 없으면 목록에 넣지 않는다
            wet = level not in (None, "없음")
            if not wet and not sky:
                continue
            if wet:
                raining += 1
            if km > NEARBY_RADIUS_KM:
                continue
            if not wet:
                level = "상공"      # 레이더에만 잡힘
            brg = bearing_deg(lat, lon, slat, slon)
            upwind = False
            if wind_dir is not None:
                # VEC(풍향)은 바람이 불어오는 방향. 그 방향의 비가 이쪽으로 온다.
                diff = abs((brg - wind_dir + 180.0) % 360.0 - 180.0)
                upwind = diff <= UPWIND_TOLERANCE_DEG

            found.append(NearbyStation(
                stn=stn, name=name_of(stn), km=round(km, 1),
                bearing=round(brg), direction=compass_name(brg),
                rn_mm=round(rec.rn_mm or 0.0, 1), upwind=upwind,
                level=level, evidence=rec.evidence, minutes=rec.re_min,
                radar_mmh=rec.radar_mmh, echo_height_m=rec.echo_height_m,
            ))

        found.sort(key=lambda s: s.km)
        # 판정 가능한 지점이 하나도 없으면 "비가 없다"가 아니라 "모른다"이다.
        self.radar_nationwide = radar_nationwide
        return (found, raining, known > 0, obs_at,
                LocalVerdict(here_known, here_rain, here_drizzle,
                             here_aloft_known, here_aloft))

    @staticmethod
    def _eta_range(km: float, wind_ms: float | None) -> tuple[int, int] | None:
        """관측소까지 거리와 지상풍으로 도착시간 범위(분)를 추정한다.

        지상풍이 거의 없으면(정체·약풍) 이동 방향 자체가 불확실하므로 추정하지 않는다.
        """
        if wind_ms is None or wind_ms < 0.5:
            return None
        base_kmh = wind_ms * 3.6
        fast = min(SYSTEM_SPEED_MAX_KMH, max(SYSTEM_SPEED_MIN_KMH, base_kmh * STEERING_RATIO_MAX))
        slow = min(SYSTEM_SPEED_MAX_KMH, max(SYSTEM_SPEED_MIN_KMH, base_kmh * STEERING_RATIO_MIN))
        lo = int(km / fast * 60)
        hi = int(km / slow * 60)
        if hi <= 0 or lo > 360:
            return None
        return max(0, lo), max(lo + 5, hi)

    @staticmethod
    def _humanize(lo: int, hi: int) -> str:
        """분 → 사람이 읽는 말. '102~107분' 보다 '1시간 40분쯤'이 낫다."""
        mid = (lo + hi) // 2
        if mid < 15:
            return "곧"
        if mid < 60:
            return f"{lo}~{hi}분 뒤"
        h, m = divmod(mid, 60)
        if m < 15:
            return f"{h}시간쯤 뒤"
        if m < 45:
            return f"{h}시간 반쯤 뒤"
        return f"{h + 1}시간쯤 뒤"

    # --- 우산 시간대 -----------------------------------------------------

    @staticmethod
    def _umbrella_window(timeline: list[RainHour]) -> str | None:
        wet = [h for h in timeline if h.is_rain]
        if not wet:
            return None
        blocks, start, prev = [], wet[0], wet[0]
        for h in wet[1:]:
            if h.at - prev.at <= timedelta(hours=1):
                prev = h
                continue
            blocks.append((start, prev))
            start = prev = h
        blocks.append((start, prev))
        return ", ".join(
            f"{a.at:%H}~{(b.at + timedelta(hours=1)):%H}시" for a, b in blocks
        )

    # --- 본체 -----------------------------------------------------------

    async def rain_at(self, lat: float, lon: float) -> RainAt:
        weights = grid_weights(lat, lon)
        precip, wind_ms, wind_dir, ncst_at = await self._now(weights)
        timeline = await self._timeline(weights)
        nearby, raining_total, obs_ok, obs_at, local = await self._nearby(
            lat, lon, wind_dir)

        here = [s for s in nearby if s.km <= HERE_RADIUS_KM]
        # 합의로 정한다. 반경 안에 판정 가능한 지점이 없으면 격자 실황으로 물러난다.
        here_level = local.level
        if here_level is None:
            here_level = "비" if (precip or 0.0) > 0.0 else "없음"
        raining_now = here_level == "비"

        # 어느 자료가 더 신선한가. 낡은 격자 실황보다 방금 들어온 관측소 실측이 낫다.
        now = datetime.now(KST)
        if here:
            ev = f" · {here[0].evidence}" if here[0].evidence else ""
            label = "" if here[0].name.isdigit() else f"{here[0].name} "
            source = f"관측소 실측({label}{here[0].km}km{ev})"
            data_at = obs_at if obs_at is not None else ncst_at
        elif precip is not None:
            candidates = [t for t in (ncst_at, obs_at) if t is not None]
            data_at = max(candidates) if candidates else None
            source = "초단기실황(격자보간)" if len(weights) > 1 else "초단기실황"
        else:
            data_at, source = None, "자료없음"

        age_min = (
            max(0, int((now - data_at).total_seconds() // 60))
            if data_at is not None else None
        )
        stale = age_min is not None and age_min > STALE_MIN

        onset = next((h.at for h in timeline if h.is_rain), None)
        clearing = None
        if onset is not None:
            after = [h for h in timeline if h.at > onset]
            dry = next((h for h in after if not h.is_rain), None)
            clearing = dry.at if dry else None

        # 바람 불어오는 쪽에서 오는 것. '비'를 우선하되, 없으면 빗방울도 알려준다 —
        # 다만 강도를 문장에서 구분하고, 약한 바람이면 그 사실도 함께 말한다.
        approaching = None
        for want in ("비", "상공", "빗방울"):
            approaching = next(
                (s for s in nearby
                 if s.upwind and s.level == want and s.km <= APPROACH_MAX_KM),
                None)
            if approaching is not None:
                break

        # 이미 오고 있는데 "102분 뒤 도착"을 같이 내보내면 화면이 모순된다
        eta = None
        if approaching is not None and here_level == "없음":
            eta = self._eta_range(approaching.km, wind_ms)
            if eta and eta[0] > ETA_HORIZON_MIN:
                eta = None          # 3시간 넘게면 분 단위로 말할 의미가 없다

        mismatch = self._mismatch(
            obs_ok=obs_ok, onset=onset, raining_now=raining_now,
            nearby=nearby, timeline=timeline,
        )

        result = RainAt(
            lat=lat, lon=lon,
            raining_now=raining_now,
            now_precip_mm=round(precip or 0.0, 1),
            now_source=source,
            grid_blend=[{"nx": g.nx, "ny": g.ny, "w": round(g.weight, 3)} for g in weights],
            timeline=timeline,
            onset_at=onset,
            clearing_at=clearing,
            umbrella_window=self._umbrella_window(timeline),
            nearest_rain=nearby[0] if nearby else None,
            approaching=approaching,
            eta_min_range=eta,
            stations_raining=raining_total,
            obs_available=obs_ok,
            obs_kind=self.obs_kind,
            level=here_level,
            local_known=local.known,
            local_rain=local.rain,
            local_drizzle=local.drizzle,
            local_aloft=local.aloft,
            local_aloft_known=local.aloft_known,
            sky_only=local.sky_only,
            radar_nationwide=self.radar_nationwide,
            wind_ms=wind_ms,
            mismatch=mismatch,
            data_at=data_at,
            data_age_min=age_min,
            stale=stale,
            updated_at=now,
        )
        result.confidence = self._confidence(result)
        result.advice = self._advice(result)
        return result

    # --- 예보 ↔ 관측 대조 -------------------------------------------------

    @staticmethod
    def _mismatch(
        *, obs_ok: bool, onset: datetime | None, raining_now: bool,
        nearby: list[NearbyStation], timeline: list[RainHour],
    ) -> str | None:
        """예보와 실제 관측이 같은 말을 하고 있는지.

        대표가 겪은 문제("하루종일 온다더니 안 온다")에 답하는 자리다.
        비 예보가 코앞인데 반경 안 어느 관측소도 비를 실측하지 않고 있다면,
        그 사실 자체가 사용자에게 값어치 있는 정보다.

        **관측을 못 받아온 경우(obs_ok=False)는 판정하지 않는다.**
        "비가 없다"와 "모른다"는 다르다 — 섞으면 없는 확신을 만들어낸다.
        """
        if not obs_ok:
            return None

        now = datetime.now(KST)
        rain_soon = (
            onset is not None
            and (onset - now).total_seconds() <= MISMATCH_HORIZON_H * 3600
        )
        anything_near = bool(nearby)

        if rain_soon and not anything_near:
            return "예보만"
        if raining_now and not any(h.is_rain for h in timeline):
            return "관측만"
        if rain_soon and anything_near:
            return "일치"
        return None

    # --- 신뢰도와 문장 ---------------------------------------------------

    @staticmethod
    def _confidence(r: RainAt) -> str:
        """자료가 서로 같은 말을 할수록 높다. 숨기지 않고 그대로 보여준다.

        오래된 자료는 그 자체로 신뢰도를 깎는다 — 관측소가 몇 곳이든 상관없이,
        40분 전 값으로 '지금'을 말하면 이미 그친 비를 현재로 보고하게 된다.
        """
        if r.data_age_min is not None and r.data_age_min > VERY_STALE_MIN:
            return "낮음"
        if r.sky_only and r.level != "비":
            # 레이더엔 잡히는데 지상 관측은 무강수 — 떨어지다 증발했거나 아직 안 닿았다.
            # 사용자에게 원리를 설명하지 않는다. 상태와 행동만 말한다.
            base = "머리 위에 비구름이 있는데 아직 땅까지는 안 닿고 있어요"
            if r.onset_at:
                return f"{base} — 예보로는 {r.onset_at:%H시%M분}부터예요."
            return f"{base}. 곧 떨어질 수도 있어요."

        if r.level == "빗방울":
            return "보통"                       # 우산이 필요한지 단정하기 어렵다
        if r.raining_now and r.now_source.startswith("관측소") and not r.stale:
            # 반경 안 지점이 하나뿐이면 합의가 아니라 단일 관측이다
            return "보통" if r.local_known <= 1 else "높음"
        # 예보는 비인데 주변 어디에도 비가 없다 → 그 예보를 그대로 믿을 근거가 없다
        if r.mismatch == "예보만":
            return "낮음"
        upwind_and_forecast = r.approaching is not None and r.onset_at is not None
        if upwind_and_forecast:
            return "보통" if r.stale else "높음"
        if r.approaching is not None or r.onset_at is not None:
            return "보통"
        return "낮음" if r.stations_raining else "보통"

    @staticmethod
    def _advice(r: RainAt) -> str:
        """행동 문장. 숫자보다 '무엇을 하면 되는지'를 먼저 말한다(제품원칙 7)."""
        if r.sky_only and r.level != "비":
            # 레이더엔 잡히는데 지상 관측은 무강수 — 떨어지다 증발했거나 아직 안 닿았다.
            # 사용자에게 원리를 설명하지 않는다. 상태와 행동만 말한다.
            base = "머리 위에 비구름이 있는데 아직 땅까지는 안 닿고 있어요"
            if r.onset_at:
                return f"{base} — 예보로는 {r.onset_at:%H시%M분}부터예요."
            return f"{base}. 곧 떨어질 수도 있어요."

        if r.level == "빗방울":
            # 2026-09-01 해운대 검증: 이 상태가 실제로는 "한두 방울" 이었다.
            # 여기서 "우산 챙기세요"라고 하면 사용자는 앱을 못 믿게 된다.
            return "빗방울이 떨어질 수 있어요 — 우산까지는 아직 아닙니다."

        if r.raining_now:
            if r.stale and r.data_at is not None:
                # 40분 전 값으로 "지금"이라고 하지 않는다. 시각을 말하면 사용자가
                # 알아서 판단한다 — 왜 늦었는지는 설명하지 않는다.
                return f"{r.data_at:%H시%M분} 기준으로 비가 내리고 있었어요."
            if r.clearing_at:
                return f"지금 비가 옵니다 — {r.clearing_at:%H시}쯤 그칠 전망이에요."
            return "지금 비가 옵니다 — 우산 챙기세요."

        if r.approaching and r.eta_min_range:
            lo, hi = r.eta_min_range
            a = r.approaching
            when = RainService._humanize(lo, hi)
            weak = (r.wind_ms is not None and r.wind_ms < WEAK_WIND_MS)

            if a.level == "비":
                tail = " 바람이 약해 더 걸릴 수도 있어요." if weak else ""
                return (f"{a.direction}쪽 {a.km:.0f}km에 비가 오고 있어요 — "
                        f"{when} 도착할 수 있습니다.{tail}")

            if a.level == "상공":
                tail = " 바람이 약해 더 걸릴 수도 있어요." if weak else ""
                return (f"{a.direction}쪽 {a.km:.0f}km에 비구름이 있어요 — "
                        f"{when} 닿을 수 있습니다.{tail}")

            # 빗방울이 다가오는 경우. 시간은 알려주되 약한 비임을 분명히 한다 —
            # 오다가 그치는 일이 흔해서 "온다"고 단정하면 헛알람이 된다.
            tail = "바람이 약해 더 걸리거나 오다 그칠 수 있어요."
            if not weak:
                tail = "약한 비라 오다 그칠 수 있어요."
            fc = f" 예보로는 {r.onset_at:%H시%M분}부터예요." if r.onset_at else ""
            return (f"{a.direction}쪽 {a.km:.0f}km에 빗방울이 관측됩니다 — "
                    f"지금 바람이면 {when}. {tail}{fc}")

        if (r.nearest_rain and r.nearest_rain.level == "빗방울"
                and not r.nearest_rain.upwind and not r.onset_at):
            # 바람 불어오는 쪽이 아니다 = 이쪽으로 오는 흐름이 아니다
            n = r.nearest_rain
            return (f"{n.direction}쪽 {n.km:.0f}km에 빗방울이 관측되지만 "
                    f"이쪽으로 오는 흐름은 아니에요.")

        if r.onset_at and r.mismatch == "예보만":
            # 관측소가 성기므로 "안 온다"고 단정하지 않는다. 있는 사실만 말한다.
            mins = (r.onset_at - datetime.now(KST)).total_seconds() / 60
            if mins <= MISMATCH_IMMINENT_H * 60:
                return (f"{r.onset_at:%H시%M분} 비 예보인데, 지금 주변 "
                        f"{int(NEARBY_RADIUS_KM)}km 안에 비가 오는 곳이 없어요 — "
                        f"늦어지거나 안 올 수 있습니다.")
            return (f"{r.onset_at:%H시%M분}쯤 비 예보. 다만 아직 주변 "
                    f"{int(NEARBY_RADIUS_KM)}km 안에는 비가 없습니다 — 지켜볼게요.")

        if r.onset_at:
            end = f" → {r.clearing_at:%H시}쯤 그침" if r.clearing_at else ""
            win = f" 우산은 {r.umbrella_window}만 챙기시면 됩니다." if r.umbrella_window else ""
            return f"{r.onset_at:%H시%M분}쯤 비가 시작될 전망{end}.{win}"

        if r.nearest_rain:
            n = r.nearest_rain
            where = "" if n.name.isdigit() else f"({n.name})"
            what = {"빗방울": "빗방울", "상공": "비구름"}.get(n.level or "", "비")
            flow = "" if n.upwind else " 이쪽으로 오는 흐름은 아니에요."
            return (f"6시간 안에 비 소식은 없습니다. "
                    f"가장 가까운 {what}은 {n.direction}쪽 {n.km:.0f}km{where}예요.{flow}")

        return "6시간 안에 비 소식 없습니다 — 우산 안 챙기셔도 됩니다."


# ---------------------------------------------------------------- 직렬화

def to_dict(r: RainAt) -> dict:
    """API 응답 형태. 구버전 앱이 무시할 수 있도록 필드 추가만 한다."""
    return {
        "raining_now": r.raining_now,
        "current_precip_mm": r.now_precip_mm,
        "source": r.now_source,
        "confidence": r.confidence,
        "advice": r.advice,
        "updated_at": r.updated_at.strftime("%H:%M") if r.updated_at else None,
        "onset_at": r.onset_at.strftime("%H:%M") if r.onset_at else None,
        "clearing_at": r.clearing_at.strftime("%H:%M") if r.clearing_at else None,
        "umbrella_window": r.umbrella_window,
        "eta_min_range": list(r.eta_min_range) if r.eta_min_range else None,
        "nearest_rain": _station_dict(r.nearest_rain),
        "approaching": _station_dict(r.approaching),
        "stations_raining_nationwide": r.stations_raining,
        "forecast_vs_observation": r.mismatch,     # 예보만 / 관측만 / 일치 / null(모름)
        "observation_available": r.obs_available,
        "observation_source": r.obs_kind,     # AWS / ASOS / 없음
        "level": r.level,                     # 비 / 빗방울 / 없음
        "drizzle": r.level == "빗방울",
        "sky_only": r.sky_only,               # 하늘엔 있는데 땅엔 안 닿음
        "local_stations": {                   # 반경 안 합의 근거 (숨기지 않는다)
            "radius_km": int(HERE_RADIUS_KM),
            "known": r.local_known,
            "rain": r.local_rain,
            "drizzle": r.local_drizzle,
            "radar_known": r.local_aloft_known,
            "radar_wet": r.local_aloft,
        },
        # 전국 레이더 지점 수. 0 이면 레이더 조회 자체가 실패한 것이고,
        # 0 이 아닌데 radar_known 이 0 이면 여기 근처 지점만 값이 없는 것이다.
        "radar_stations_nationwide": r.radar_nationwide,
        "data_at": r.data_at.strftime("%H:%M") if r.data_at else None,
        "data_age_min": r.data_age_min,
        "stale": r.stale,
        "nearby_radius_km": int(NEARBY_RADIUS_KM),
        "grid_blend": r.grid_blend,
        "hourly": [
            {
                "time": h.at.strftime("%H:%M"),
                "in_hours": h.in_hours,
                "pty": h.pty or "없음",
                "sky": h.sky,
                "precip_mm": h.precip_mm,
                "is_rain": h.is_rain,
            }
            for h in r.timeline
        ],
    }


def _station_dict(s: NearbyStation | None) -> dict | None:
    if s is None:
        return None
    return {
        "station": s.stn, "name": s.name, "km": s.km,
        "direction": s.direction, "bearing": s.bearing,
        "precip_mm": s.rn_mm, "upwind": s.upwind, "level": s.level,
        "radar_mmh": s.radar_mmh, "echo_height_m": s.echo_height_m,
        "evidence": s.evidence,          # 현천 / 강수감지 / 우량계
        "rain_minutes_1h": s.minutes,    # RE_SUM — 최근 1시간 중 감지된 분
    }
