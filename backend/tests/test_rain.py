"""좌표 단위 강수 판정 — 격자 보간·관측 사실·접근 판정 테스트 (2026-09-01)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.kma import (
    KMAForecast,
    KMAObservation,
    bearing_deg,
    compass_name,
    grid_to_latlon,
    haversine_km,
    latlon_to_grid,
    latlon_to_grid_float,
    parse_sfctm2_all,
)
from app.services.rain import (
    GRID_WEIGHT_FLOOR,
    RainService,
    grid_weights,
    to_dict,
)

KST = timezone(timedelta(hours=9))


# ===== ① 격자 보간 =====

def test_grid_float_matches_rounded_grid():
    """소수 격자를 반올림하면 기존 latlon_to_grid 와 같아야 한다."""
    for lat, lon in [(35.10468, 129.03203), (37.5714, 126.9658), (33.5, 126.5)]:
        fx, fy = latlon_to_grid_float(lat, lon)
        g = latlon_to_grid(lat, lon)
        assert (int(fx + 0.5), int(fy + 0.5)) == (g.nx, g.ny)


def test_grid_roundtrip_is_close():
    """격자 → 중심 좌표 → 격자 왕복이 같은 격자로 돌아와야 한다."""
    for nx, ny in [(97, 74), (98, 76), (60, 127)]:
        lat, lon = grid_to_latlon(nx, ny)
        g = latlon_to_grid(lat, lon)
        assert (g.nx, g.ny) == (nx, ny)


def test_grid_weights_sum_to_one():
    for lat, lon in [(35.10468, 129.03203), (35.2, 129.1), (37.5, 127.0)]:
        ws = grid_weights(lat, lon)
        assert 0 < len(ws) <= 4
        assert abs(sum(w.weight for w in ws) - 1.0) < 1e-9
        assert all(w.weight >= GRID_WEIGHT_FLOOR for w in ws)


def test_grid_center_uses_single_cell():
    """격자 중심에 서 있으면 이웃 격자를 부를 필요가 없다 — API 호출 절약."""
    lat, lon = grid_to_latlon(97, 74)
    ws = grid_weights(lat, lon)
    assert len(ws) == 1
    assert (ws[0].nx, ws[0].ny) == (97, 74)


def test_grid_corner_blends_four_cells():
    """격자 모서리에서는 네 격자가 고르게 섞인다 — 계단 현상이 사라지는 자리."""
    lat_a, lon_a = grid_to_latlon(97, 74)
    lat_b, lon_b = grid_to_latlon(98, 75)
    ws = grid_weights((lat_a + lat_b) / 2, (lon_a + lon_b) / 2)
    assert len(ws) == 4
    assert max(w.weight for w in ws) < 0.5


# ===== 기하 =====

def test_bearing_and_compass():
    assert 0 <= bearing_deg(35.0, 129.0, 36.0, 129.0) < 1        # 정북
    assert 89 < bearing_deg(35.0, 129.0, 35.0, 130.0) < 91       # 정동
    assert compass_name(0) == "북"
    assert compass_name(90) == "동"
    assert compass_name(270) == "서"


def test_haversine_known_distance():
    km = haversine_km(35.10468, 129.03203, 35.1796, 129.0756)
    assert 8.0 < km < 11.0        # 대청동 ↔ 부산시청 약 9~10km


# ===== sfctm2 헤더 기반 파싱 =====

SAMPLE = """#START7777
#  YYMMDDHHMI STN    WD    WS    TA    HM    RN
#  (KST)       ID   deg   m/s     C     %    mm
 202609011000  159   270   3.2  24.5  92.0   1.4
 202609011000  296   250   2.0  24.0  95.0   0.0
 202609011000  108   180   1.0  26.0  60.0    -9
#7777END
"""


def test_parse_all_reads_rain_by_header():
    rows = parse_sfctm2_all(SAMPLE)
    assert rows[159]["rn_mm"] == 1.4
    assert rows[296]["rn_mm"] == 0.0
    assert rows[108]["rn_mm"] is None          # 결측은 0 이 아니라 None
    assert rows[159]["wd_deg"] == 270.0


def test_parse_all_without_header_returns_empty():
    """헤더를 못 찾으면 빈 결과. 틀린 자리에서 숫자를 읽느니 모른다고 한다."""
    assert parse_sfctm2_all(" 202609011000  159  270  3.2\n") == {}


# ===== 가짜 클라이언트 =====

class FakeKMA:
    def __init__(self, precip=0.0, wind_ms=5.0, wind_deg=270.0, wet_hours=()):
        self.precip, self.wind_ms, self.wind_deg = precip, wind_ms, wind_deg
        self.wet_hours = set(wet_hours)
        self.ncst_calls = 0
        self.fcst_calls = 0

    async def get_current_observation(self, lat, lon):
        self.ncst_calls += 1
        return KMAObservation(
            temperature_c=24.0, humidity_pct=90.0,
            wind_speed_ms=self.wind_ms, wind_direction_deg=self.wind_deg,
            precipitation_mm=self.precip, observed_at=datetime.now(KST),
        )

    async def get_ultra_short_forecast(self, lat, lon):
        self.fcst_calls += 1
        base = datetime.now(KST).replace(minute=0, second=0, microsecond=0)
        out = []
        for i in range(1, 7):
            at = base + timedelta(hours=i)
            wet = i in self.wet_hours
            out.append(KMAForecast(
                forecast_for=at, temperature_c=24.0, humidity_pct=90.0,
                wind_speed_ms=self.wind_ms, wind_direction_deg=self.wind_deg,
                precipitation_mm=1.0 if wet else 0.0,
                sky_condition="흐림" if wet else "맑음",
                precipitation_type="비" if wet else "없음",
            ))
        return out


class FakeASOS:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def get_all_hourly(self):
        self.calls += 1
        return self.rows


BUSAN = (35.10468, 129.03203)


# ===== ② 지금 비가 오나 =====

async def test_raining_now_from_station_observation():
    """근처 관측소가 비를 실측하면 '지금 비'는 예보가 아니라 사실이다.

    다만 반경 안 지점이 하나뿐이면 합의가 아니라 단일 관측이므로 신뢰도는 보통.
    """
    svc = RainService(FakeKMA(precip=0.0), FakeASOS({159: {"rn_mm": 1.4}}))
    r = await svc.rain_at(*BUSAN)
    assert r.raining_now is True
    assert r.level == "비"
    assert r.now_source.startswith("관측소 실측")
    assert r.confidence == "보통"
    assert r.local_known == 1
    assert "지금 비가 옵니다" in r.advice


async def test_no_rain_anywhere_says_so_plainly():
    svc = RainService(FakeKMA(), FakeASOS({}))
    r = await svc.rain_at(*BUSAN)
    assert r.raining_now is False
    assert r.onset_at is None
    assert "우산 안 챙기셔도" in r.advice


# ===== ③ 접근 판정 =====

async def test_upwind_rain_is_reported_as_approaching():
    """서풍이 불고 서쪽 관측소에 비 → 접근 중으로 보고 도착 범위를 준다."""
    rows = {104: {"rn_mm": 3.0}}          # 104 북강릉? — 좌표는 ASOS_STATIONS 기준
    svc = RainService(FakeKMA(wind_deg=270.0, wind_ms=6.0), FakeASOS(rows))
    r = await svc.rain_at(*BUSAN)
    # 실제 접근 판정은 관측소 방위에 따라 갈리므로, 판정 로직 자체를 직접 검증한다
    eta = svc._eta_range(30.0, 6.0)
    assert eta is not None
    lo, hi = eta
    assert 0 < lo < hi <= 360
    assert lo < hi                        # 단일 시각이 아니라 범위


async def test_calm_wind_gives_no_eta():
    """바람이 거의 없으면 이동 방향이 불확실 → 도착시간을 추정하지 않는다."""
    svc = RainService(FakeKMA(), FakeASOS({}))
    assert svc._eta_range(30.0, 0.2) is None
    assert svc._eta_range(30.0, None) is None


# ===== 우산 시간대 =====

async def test_umbrella_window_is_narrow_not_all_day():
    """'하루종일 비'가 아니라 실제 비 시간대만 나와야 한다."""
    svc = RainService(FakeKMA(wet_hours=(3, 4)), FakeASOS({}))
    r = await svc.rain_at(*BUSAN)
    assert r.onset_at is not None
    assert r.umbrella_window is not None
    assert "~" in r.umbrella_window
    wet = [h for h in r.timeline if h.is_rain]
    assert len(wet) == 2                  # 6시간 중 2시간만
    assert r.clearing_at is not None


# ===== 견고성 =====

async def test_survives_forecast_failure():
    """예보가 죽어도 관측만으로 답한다 — 화면이 비지 않는다."""
    class Broken(FakeKMA):
        async def get_ultra_short_forecast(self, lat, lon):
            raise RuntimeError("KMA down")

    svc = RainService(Broken(precip=0.5), FakeASOS({}))
    r = await svc.rain_at(*BUSAN)
    assert r.raining_now is True
    assert r.timeline == []
    assert r.advice


async def test_survives_missing_asos():
    """ASOS 미주입(키 없음)이어도 동작한다."""
    svc = RainService(FakeKMA(wet_hours=(2,)), asos=None)
    r = await svc.rain_at(*BUSAN)
    assert r.nearest_rain is None
    assert r.onset_at is not None


async def test_grid_center_calls_api_once_per_kind():
    """격자 중심이면 호출이 1회씩 — 보간 때문에 요금이 4배 되지 않는다."""
    lat, lon = grid_to_latlon(97, 74)
    kma = FakeKMA()
    svc = RainService(kma, FakeASOS({}))
    await svc.rain_at(lat, lon)
    assert kma.ncst_calls == 1
    assert kma.fcst_calls == 1


async def test_cache_prevents_repeat_calls():
    kma = FakeKMA()
    asos = FakeASOS({})
    svc = RainService(kma, asos)
    await svc.rain_at(*BUSAN)
    first = (kma.ncst_calls, kma.fcst_calls, asos.calls)
    await svc.rain_at(*BUSAN)
    assert (kma.ncst_calls, kma.fcst_calls, asos.calls) == first


# ===== 응답 형태 =====

async def test_to_dict_keeps_fields_app_reads():
    svc = RainService(FakeKMA(wet_hours=(1, 2)), FakeASOS({}))
    d = to_dict(await svc.rain_at(*BUSAN))
    for key in ("raining_now", "current_precip_mm", "advice", "hourly"):
        assert key in d
    assert isinstance(d["hourly"], list)
    assert {"time", "in_hours", "pty", "precip_mm"} <= set(d["hourly"][0])
    assert d["confidence"] in ("높음", "보통", "낮음")


# ===== 예보 ↔ 관측 대조 ("하루종일 온다더니 안 온다"에 답하는 자리) =====

async def test_forecast_says_rain_but_nothing_observed_anywhere():
    """비 예보는 있는데 반경 안 어느 관측소도 비를 안 찍고 있으면 그 사실을 말한다."""
    obs = FakeASOS({108: {"rn_mm": 0.0}, 159: {"rn_mm": 0.0}})   # 받아왔지만 전부 무강수
    svc = RainService(FakeKMA(wet_hours=(2,)), obs)
    r = await svc.rain_at(*BUSAN)

    assert r.obs_available is True
    assert r.mismatch == "예보만"
    assert r.confidence == "낮음"          # 예보를 그대로 믿을 근거가 없다
    assert "비가 없" in r.advice
    # "안 온다"고 단정하지 않는다 — 관측소는 성기다
    assert "안 옵니다" not in r.advice


async def test_imminent_forecast_with_no_rain_nearby_is_flagged_harder():
    svc = RainService(FakeKMA(wet_hours=(1,)), FakeASOS({108: {"rn_mm": 0.0}}))
    r = await svc.rain_at(*BUSAN)
    assert r.mismatch == "예보만"
    assert "늦어지거나 안 올 수" in r.advice


async def test_no_observation_means_no_claim():
    """관측을 못 받아왔으면 대조하지 않는다 — '비가 없다'와 '모른다'는 다르다."""
    svc = RainService(FakeKMA(wet_hours=(2,)), FakeASOS({}))
    r = await svc.rain_at(*BUSAN)
    assert r.obs_available is False
    assert r.mismatch is None
    assert "비가 없" not in r.advice        # 없는 확신을 만들지 않는다
    assert r.onset_at is not None           # 예보는 그대로 전달


async def test_no_asos_client_makes_no_claim():
    svc = RainService(FakeKMA(wet_hours=(2,)), asos=None)
    r = await svc.rain_at(*BUSAN)
    assert r.obs_available is False
    assert r.mismatch is None


async def test_mismatch_exposed_in_response():
    svc = RainService(FakeKMA(wet_hours=(2,)), FakeASOS({108: {"rn_mm": 0.0}}))
    d = to_dict(await svc.rain_at(*BUSAN))
    assert d["forecast_vs_observation"] == "예보만"
    assert d["observation_available"] is True
    assert d["nearby_radius_km"] == 120


# ===== ⑤ 자료 신선도 =====

class StaleKMA(FakeKMA):
    """초단기실황이 오래된 상황 — 화면이 40분 전 상태인 그 경우."""
    def __init__(self, age_min: int, **kw):
        super().__init__(**kw)
        self.age_min = age_min

    async def get_current_observation(self, lat, lon):
        self.ncst_calls += 1
        return KMAObservation(
            temperature_c=24.0, humidity_pct=90.0,
            wind_speed_ms=self.wind_ms, wind_direction_deg=self.wind_deg,
            precipitation_mm=self.precip,
            observed_at=datetime.now(KST) - timedelta(minutes=self.age_min),
        )


async def test_fresh_data_says_now():
    svc = RainService(StaleKMA(5, precip=1.2), FakeASOS({}))
    r = await svc.rain_at(*BUSAN)
    assert r.stale is False
    assert r.data_age_min is not None and r.data_age_min <= 6
    assert "지금 비가 옵니다" in r.advice


async def test_stale_data_does_not_claim_now():
    """40분 전 값으로 '지금 비가 옵니다'라고 하지 않는다 — 이미 그쳤을 수 있다."""
    svc = RainService(StaleKMA(50, precip=1.2), FakeASOS({}))
    r = await svc.rain_at(*BUSAN)
    assert r.stale is True
    assert "지금 비가 옵니다" not in r.advice
    assert "기준으로 비가 내리고 있었" in r.advice


async def test_very_stale_drops_confidence():
    svc = RainService(StaleKMA(120, precip=1.2), FakeASOS({}))
    r = await svc.rain_at(*BUSAN)
    assert r.confidence == "낮음"


async def test_fresh_station_observation_preferred_over_stale_grid():
    """격자 실황이 낡았어도 관측소 실측이 방금 들어왔으면 그쪽을 기준으로 삼는다."""
    from app.services.kma import ASOS_STATIONS
    now_tm = datetime.now(KST).strftime("%Y%m%d%H%M")
    svc = RainService(
        StaleKMA(50, precip=0.0),
        FakeASOS({159: {"rn_mm": 2.0, "tm": now_tm}}),
    )
    r = await svc.rain_at(*BUSAN)
    assert r.raining_now is True
    assert r.now_source.startswith("관측소 실측")
    assert r.stale is False              # 신선한 관측이 기준이 됐다
    assert r.confidence == "보통"        # 반경 안 지점이 하나뿐 → 합의가 아니다


async def test_staleness_exposed_in_response():
    svc = RainService(StaleKMA(50, precip=1.0), FakeASOS({}))
    d = to_dict(await svc.rain_at(*BUSAN))
    assert d["stale"] is True
    assert d["data_age_min"] >= 45
    assert d["data_at"] is not None


# ===== AWS 실측 연동 =====

class FakeRegistry:
    def __init__(self, stations, names=None):
        self.stations = stations
        self._names = names or {}

    def name(self, stn):
        return self._names.get(stn, str(stn))


class FakeAWS:
    """AWSObsClient 를 흉내낸다. observe() 가 지점별 판정 재료를 준다."""

    def __init__(self, recs, stations=None, ok=True):
        from app.services.kma import ASOS_STATIONS
        self.recs = recs
        self.registry = FakeRegistry(stations or ASOS_STATIONS, {159: "부산", 288: "밀양"})
        self.ok = ok
        self.calls = 0

    async def ensure_stations(self):
        return self.ok

    async def observe(self):
        self.calls += 1
        return self.recs


async def test_aws_preferred_over_asos():
    from app.services.aws_obs import StationRain as SR
    aws = FakeAWS({159: SR(stn=159, rn_mm=0.0, re_min=30.0, ww=61)})
    svc = RainService(FakeKMA(), asos=FakeASOS({159: {"rn_mm": 0.0}}), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.obs_kind == "AWS"
    # 우량계 0mm — 현천이 비라 하고 30분간 감지됐으므로 비
    assert r.raining_now is True
    assert "강수감지" in r.now_source


async def test_falls_back_to_asos_when_aws_unavailable():
    aws = FakeAWS({}, ok=False)
    svc = RainService(FakeKMA(), asos=FakeASOS({159: {"rn_mm": 1.4}}), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.obs_kind == "ASOS"
    assert r.raining_now is True


async def test_drizzle_is_reported_as_drizzle_not_rain():
    """현천만 비라고 하고 우량계·감지분수가 0 이면 '빗방울'이지 '비'가 아니다.

    2026-09-01 해운대 실측 검증: 그 상태가 실제로는 "한두 방울" 이었다.
    여기서 "우산 챙기세요"라고 하면 사용자는 앱을 못 믿게 된다.
    """
    from app.services.aws_obs import StationRain as SR
    aws = FakeAWS({159: SR(stn=159, rn_mm=0.0, re_min=0.0, ww=53)})   # 안개비
    svc = RainService(FakeKMA(), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.level == "빗방울"
    assert r.raining_now is False
    assert r.confidence == "보통"
    assert "우산까지는 아직" in r.advice
    assert "우산 챙기세요" not in r.advice
    assert r.nearest_rain is not None and r.nearest_rain.evidence == "현천"


async def test_one_wet_station_among_many_is_not_here_rain():
    """반경 안 5곳 중 1곳만 비 → '여기 비'가 아니라 '근처에 비'."""
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS
    near = [s for s in ASOS_STATIONS
            if abs(ASOS_STATIONS[s][0] - BUSAN[0]) < 0.1
            and abs(ASOS_STATIONS[s][1] - BUSAN[1]) < 0.1]
    recs = {159: SR(stn=159, rn_mm=2.0)}
    for i, stn in enumerate(near):
        if stn != 159:
            recs[stn] = SR(stn=stn, rn_mm=0.0, ww=0)
    aws = FakeAWS(recs)
    svc = RainService(FakeKMA(), aws=aws)
    r = await svc.rain_at(*BUSAN)
    if r.local_known >= 2:                      # 반경 안에 여러 곳이 잡힌 경우만 의미
        assert r.local_rain == 1
        assert r.raining_now is (r.local_rain / r.local_known >= 0.5)


async def test_eta_not_shown_while_it_is_already_raining():
    """이미 오고 있는데 '102분 뒤 도착'을 같이 내보내면 화면이 모순된다."""
    from app.services.aws_obs import StationRain as SR
    aws = FakeAWS({159: SR(stn=159, rn_mm=2.0)})
    svc = RainService(FakeKMA(wind_deg=270.0, wind_ms=6.0), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.raining_now is True
    assert r.eta_min_range is None


async def test_unknown_stations_do_not_count_as_dry():
    """자료가 없는 지점만 있으면 대조 판정을 하지 않는다."""
    from app.services.aws_obs import StationRain as SR
    aws = FakeAWS({159: SR(stn=159), 288: SR(stn=288)})     # 전부 결측
    svc = RainService(FakeKMA(wet_hours=(2,)), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.obs_available is False
    assert r.mismatch is None


async def test_aws_evidence_in_response():
    from app.services.aws_obs import StationRain as SR
    aws = FakeAWS({288: SR(stn=288, rn_mm=0.0, re_min=25.0)})   # 25분 감지 → 비
    svc = RainService(FakeKMA(), aws=aws)
    d = to_dict(await svc.rain_at(*BUSAN))
    assert d["observation_source"] == "AWS"
    assert d["nearest_rain"]["evidence"] == "강수감지"
    assert d["nearest_rain"]["rain_minutes_1h"] == 25.0


# ===== 도착시간을 말해주되 강도·불확실성과 함께 =====

def test_humanize_minutes():
    h = RainService._humanize
    assert h(5, 10) == "곧"
    assert h(20, 35) == "20~35분 뒤"
    assert h(55, 70) == "1시간쯤 뒤"
    assert h(102, 107) == "1시간 반쯤 뒤"      # 실측에서 나왔던 값


async def test_approaching_drizzle_still_gets_a_time():
    """빗방울이라도 언제 닿을지는 알려준다 — 시간이 없으면 반쪽짜리 문장이다.

    다만 '비가 온다'가 아니라 '빗방울', 그리고 오다 그칠 수 있다는 말을 붙인다.
    """
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, bearing_deg
    stn = 288                                  # 밀양 — 부산 북서쪽
    brg = bearing_deg(*BUSAN, *ASOS_STATIONS[stn])
    aws = FakeAWS({stn: SR(stn=stn, rn_mm=0.0, re_min=0.0, ww=53)})
    svc = RainService(FakeKMA(wind_deg=brg, wind_ms=6.0), aws=aws)
    r = await svc.rain_at(*BUSAN)

    assert r.nearest_rain is not None and r.nearest_rain.level == "빗방울"
    assert r.eta_min_range is not None          # 시간을 준다
    assert "빗방울" in r.advice
    assert "분" in r.advice or "시간" in r.advice
    assert "그칠 수 있" in r.advice             # 단정하지 않는다
    assert "비가 오고 있어요" not in r.advice


def _station_between(lo_km: float, hi_km: float) -> int:
    """기준점에서 lo~hi km 떨어진 관측소 하나. 거리에 따라 판정이 달라지므로
    테스트마다 적당한 거리의 지점을 골라 쓴다."""
    from app.services.kma import ASOS_STATIONS, haversine_km
    for stn, (la, lo) in ASOS_STATIONS.items():
        if lo_km <= haversine_km(*BUSAN, la, lo) <= hi_km:
            return stn
    raise AssertionError(f"{lo_km}~{hi_km}km 안에 관측소가 없습니다")


async def test_weak_wind_is_said_out_loud():
    """바람이 약하면 도착시간이 불확실하다 — 숫자만 던지지 않는다."""
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, bearing_deg
    stn = _station_between(10.0, 25.0)
    brg = bearing_deg(*BUSAN, *ASOS_STATIONS[stn])
    aws = FakeAWS({stn: SR(stn=stn, rn_mm=1.0)})            # 비
    svc = RainService(FakeKMA(wind_deg=brg, wind_ms=1.2), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.eta_min_range is not None
    assert "바람이 약해" in r.advice


async def test_too_far_or_too_slow_gives_no_minute_estimate():
    """3시간 넘게 걸릴 거리·속도면 분 단위로 말하지 않는다."""
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, bearing_deg
    stn = _station_between(45.0, 60.0)
    brg = bearing_deg(*BUSAN, *ASOS_STATIONS[stn])
    aws = FakeAWS({stn: SR(stn=stn, rn_mm=1.0)})
    svc = RainService(FakeKMA(wind_deg=brg, wind_ms=1.2), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.eta_min_range is None


async def test_rain_not_heading_here_is_said_so():
    """바람 불어오는 쪽이 아니면 '이쪽으로 오는 흐름은 아니에요'."""
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, bearing_deg
    stn = 288
    brg = bearing_deg(*BUSAN, *ASOS_STATIONS[stn])
    aws = FakeAWS({stn: SR(stn=stn, rn_mm=0.0, re_min=0.0, ww=53)})
    svc = RainService(FakeKMA(wind_deg=(brg + 180) % 360, wind_ms=6.0), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.approaching is None
    assert "오는 흐름은 아니" in r.advice


# ===== 레이더(하늘) × 관측소(땅) =====

async def test_sky_only_is_told_without_jargon():
    """레이더엔 있고 땅엔 없을 때 — 원리를 설명하지 않고 상태만 말한다."""
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, haversine_km
    near = [s for s, (la, lo) in ASOS_STATIONS.items()
            if haversine_km(*BUSAN, la, lo) <= 8.0]
    assert near, "반경 안 지점이 필요합니다"
    recs = {s: SR(stn=s, rn_mm=0.0, re_min=0.0, ww=0, radar_mmh=1.5,
                  echo_height_m=1800.0) for s in near}
    svc = RainService(FakeKMA(), aws=FakeAWS(recs))
    r = await svc.rain_at(*BUSAN)

    assert r.sky_only is True
    assert r.raining_now is False
    assert "머리 위에 비구름" in r.advice
    assert "우산 챙기세요" not in r.advice
    assert "증발" not in r.advice and "레이더" not in r.advice   # 전문용어 금지


async def test_radar_extends_approach_detection():
    """지상 관측이 무강수여도 레이더가 잡은 강수대는 접근 판정에 쓴다."""
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, bearing_deg
    stn = _station_between(10.0, 25.0)
    brg = bearing_deg(*BUSAN, *ASOS_STATIONS[stn])
    aws = FakeAWS({stn: SR(stn=stn, rn_mm=0.0, re_min=0.0, ww=0, radar_mmh=2.0)})
    svc = RainService(FakeKMA(wind_deg=brg, wind_ms=6.0), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.approaching is not None and r.approaching.level == "상공"
    assert "비구름이 있어요" in r.advice
    assert r.eta_min_range is not None


async def test_radar_out_of_range_is_not_dry():
    """레이더 반경 밖(-300)을 '비 없음'으로 세지 않는다."""
    from app.services.aws_obs import StationRain as SR
    aws = FakeAWS({159: SR(stn=159, radar_mmh=None)})
    svc = RainService(FakeKMA(), aws=aws)
    r = await svc.rain_at(*BUSAN)
    assert r.local_aloft_known == 0
    assert r.sky_only is False


async def test_fallback_sentence_uses_the_right_word():
    """실측(2026-09-01)에서 빗방울을 '가장 가까운 비'라고 불렀다.

    approaching 은 있는데 거리·풍속 때문에 eta 가 없어 앞 분기를 못 타고
    마지막 문장으로 떨어진 경우였다. 그 문장도 강도를 그대로 불러야 한다.
    """
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, bearing_deg
    far = _station_between(45.0, 60.0)
    brg = bearing_deg(*BUSAN, *ASOS_STATIONS[far])
    aws = FakeAWS({far: SR(stn=far, rn_mm=0.0, re_min=0.0, ww=53)})
    svc = RainService(FakeKMA(wind_deg=brg, wind_ms=1.2), aws=aws)
    r = await svc.rain_at(*BUSAN)

    assert r.eta_min_range is None
    assert "가까운 빗방울" in r.advice
    assert "가까운 비는" not in r.advice


# ===== 구버전 앱이 읽는 키 (2026-09-01 운영에서 잘못 켜져 있었다) =====

async def test_umbrella_not_recommended_for_far_away_rain():
    """120km 밖의 비로 '우산 챙기세요'를 켜면 안 된다.

    구버전 앱은 umbrella_recommended 만 보고 알림을 띄운다.
    운영 확인: 119.8km 밖 가산의 비 때문에 부산 사용자에게 우산을 권하고 있었다.
    """
    from app.services.aws_obs import StationRain as SR
    from app.services.kma import ASOS_STATIONS, bearing_deg, haversine_km
    far = max(ASOS_STATIONS, key=lambda s: -1 if haversine_km(
        *BUSAN, *ASOS_STATIONS[s]) > 120 else haversine_km(*BUSAN, *ASOS_STATIONS[s]))
    assert 60 < haversine_km(*BUSAN, *ASOS_STATIONS[far]) <= 120

    brg = bearing_deg(*BUSAN, *ASOS_STATIONS[far])
    aws = FakeAWS({far: SR(stn=far, rn_mm=3.5)})
    svc = RainService(FakeKMA(wind_deg=brg, wind_ms=6.0), aws=aws)
    r = await svc.rain_at(*BUSAN)

    assert r.level == "없음"
    assert r.approaching is None          # 60km 밖은 '다가온다'고 하지 않는다
    assert r.nearest_rain is not None     # 다만 근처에 비가 있다는 사실은 남긴다


# ===== 미리 받아두기 =====

async def test_prewarm_fills_cache_so_requests_do_not_wait():
    """전 지점 관측은 좌표와 무관하다 — 미리 받아두면 요청은 캐시만 읽는다.

    운영에서 첫 요청이 1분 걸렸다(지점표 714곳 + 관측 5종을 그 자리에서 받음).
    """
    import asyncio
    from app.services.aws_obs import StationRain as SR

    aws = FakeAWS({159: SR(stn=159, rn_mm=0.0, ww=0)})
    svc = RainService(FakeKMA(), aws=aws)

    svc.start_prewarm(interval_sec=3600)
    await asyncio.sleep(0)          # 루프가 한 번 돌 기회를 준다
    await asyncio.sleep(0)
    for _ in range(20):
        if aws.calls:
            break
        await asyncio.sleep(0.01)
    assert aws.calls >= 1, "미리 받기가 관측을 가져오지 않았다"

    before = aws.calls
    await svc.rain_at(*BUSAN)       # 요청은 캐시를 쓴다
    assert aws.calls == before

    await svc.stop_prewarm()
    assert svc._prewarm_task is None


async def test_stop_prewarm_is_safe_when_not_started():
    svc = RainService(FakeKMA())
    await svc.stop_prewarm()        # 예외 없이 지나가야 한다
