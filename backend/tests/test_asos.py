"""ASOS 실시간 지상관측(API허브 kma_sfctm2) — 파서·운량 역산·관측소 선택 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.kma import (
    ASOS_STATIONS,
    ASOSClient,
    ASOSObservation,
    parse_sfctm2,
)
from vpti_core.solar import cloud_fraction_from_obs_ghi, kasten_czeplak_factor

KST = timezone(timedelta(hours=9))


def _make_line(
    tm: str = "202608111000",
    stn: str = "159",
    ws: str = "3.8",
    ta: str = "27.1",
    hm: str = "74",
    ca_tot: str = "10",
    si: str = "0.39",
    ts: str = "29.0",
) -> str:
    """kma_sfctm2 컬럼(46개) 데이터 행 합성 — 문서 컬럼 순서 그대로."""
    tokens = ["-9"] * 46
    tokens[0] = tm       # TM
    tokens[1] = stn      # STN
    tokens[2] = "70"     # WD
    tokens[3] = ws       # WS
    tokens[11] = ta      # TA
    tokens[13] = hm      # HM
    tokens[25] = ca_tot  # CA_TOT
    tokens[34] = si      # SI
    tokens[36] = ts      # TS
    return " ".join(tokens)


SAMPLE = "\n".join([
    "#START7777",
    "# 주석 헤더",
    _make_line(),
    "#7777END",
])


class TestParseSfctm2:
    def test_기본_파싱(self):
        obs = parse_sfctm2(SAMPLE, 159)
        assert obs is not None
        assert obs.station_id == 159
        assert obs.observed_at == datetime(2026, 8, 11, 10, 0, tzinfo=KST)
        assert obs.temperature_c == pytest.approx(27.1)
        assert obs.humidity_pct == pytest.approx(74.0)
        assert obs.wind_speed_ms == pytest.approx(3.8)
        assert obs.cloud_cover_tenths == pytest.approx(10.0)
        assert obs.solar_mj == pytest.approx(0.39)
        assert obs.ground_temp_c == pytest.approx(29.0)

    def test_결측값은_None(self):
        text = _make_line(si="-9.0", ca_tot="-9", ts="-99.0")
        obs = parse_sfctm2(text, 159)
        assert obs is not None
        assert obs.solar_mj is None
        assert obs.cloud_cover_tenths is None
        assert obs.ground_temp_c is None

    def test_범위_밖_값은_None(self):
        obs = parse_sfctm2(_make_line(ta="99.0", ca_tot="15"), 159)
        assert obs is not None
        assert obs.temperature_c is None       # 55°C 초과
        assert obs.cloud_cover_tenths is None  # 10 초과

    def test_다른_관측소는_무시(self):
        assert parse_sfctm2(SAMPLE, 108) is None

    def test_주석과_빈줄만이면_None(self):
        assert parse_sfctm2("#START7777\n#7777END\n", 159) is None

    def test_여러_행이면_최신_시각(self):
        text = "\n".join([
            _make_line(tm="202608110900", ta="25.0"),
            _make_line(tm="202608111000", ta="27.1"),
        ])
        obs = parse_sfctm2(text, 159)
        assert obs.observed_at.hour == 10
        assert obs.temperature_c == pytest.approx(27.1)

    def test_solar_avg_wm2_변환(self):
        obs = parse_sfctm2(_make_line(si="3.6"), 159)
        # 3.6 MJ/m²·h = 1000 W/m² 평균
        assert obs.solar_avg_wm2 == pytest.approx(1000.0)


class TestCloudFractionInversion:
    """실측 GHI → 유효 운량 역산 (부산 관측소, 여름 정오 부근)."""

    LAT, LON = ASOS_STATIONS[159]
    NOON = datetime(2026, 8, 11, 13, 0, tzinfo=KST)  # 12~13시 누적 구간

    def test_청천이면_0(self):
        # 청천 평균과 같은 관측 → kc=1 → CF=0
        cf = cloud_fraction_from_obs_ghi(self.LAT, self.LON, self.NOON, 2000.0)
        assert cf == pytest.approx(0.0)

    def test_완전_감쇠면_1에_가까움(self):
        cf = cloud_fraction_from_obs_ghi(self.LAT, self.LON, self.NOON, 0.0)
        assert cf == pytest.approx(1.0, abs=0.15)

    def test_단조성(self):
        cfs = [
            cloud_fraction_from_obs_ghi(self.LAT, self.LON, self.NOON, g)
            for g in (100.0, 300.0, 500.0, 700.0)
        ]
        assert all(a >= b for a, b in zip(cfs, cfs[1:]))

    def test_야간이면_None(self):
        midnight = datetime(2026, 8, 11, 2, 0, tzinfo=KST)
        assert cloud_fraction_from_obs_ghi(self.LAT, self.LON, midnight, 0.0) is None

    def test_역산_왕복_일관성(self):
        """CF → Kasten-Czeplak 감쇠 → 역산 CF 가 원래 값과 일치."""
        for cf_true in (0.25, 0.70, 0.95):
            factor = kasten_czeplak_factor(cf_true)
            # 어떤 청천값이든 관측 = 청천×factor 면 역산이 cf_true 복원
            # (역산 내부의 청천 평균을 직접 알 수 없으므로 두 번 호출로 왕복 확인)
            cf0 = cloud_fraction_from_obs_ghi(self.LAT, self.LON, self.NOON, 0.0)
            base = cloud_fraction_from_obs_ghi(self.LAT, self.LON, self.NOON, 1.0)
            assert cf0 is not None and base is not None
            # 청천 평균 근사 역추출: kc = 1 - a·cf^b 관계 검증용
            # 간접 검증: factor 비율만큼 감쇠한 관측을 넣어 근사 복원되는지
            # 청천 평균을 이분법으로 찾음
            lo, hi = 100.0, 1500.0
            for _ in range(40):
                mid = (lo + hi) / 2
                if cloud_fraction_from_obs_ghi(self.LAT, self.LON, self.NOON, mid) > 0:
                    lo = mid
                else:
                    hi = mid
            ghi_clear = (lo + hi) / 2
            cf_back = cloud_fraction_from_obs_ghi(
                self.LAT, self.LON, self.NOON, ghi_clear * factor
            )
            assert cf_back == pytest.approx(cf_true, abs=0.03)


class TestNearestStation:
    def test_부산_시내는_159(self):
        assert ASOSClient.nearest_station(35.18, 129.08) == 159

    def test_서울은_이제_서울_관측소(self):
        """2026-08-26 전국확장(97곳) 이후 서울에도 관측소가 있다.

        부산 한 곳만 있던 시절엔 None 이 맞았다. 코드가 아니라 기대값이 낡았다.
        """
        assert ASOSClient.nearest_station(37.57, 126.98) == 108

    def test_더_가까운_관측소를_고른다(self):
        """김해 인근(35.30, 128.80)은 부산(159)보다 김해시(253)가 가깝다."""
        from app.services.kma import ASOS_STATIONS, haversine_km
        here = (35.30, 128.80)
        picked = ASOSClient.nearest_station(*here)
        assert picked == 253
        d_picked = haversine_km(*here, *ASOS_STATIONS[picked])
        d_busan = haversine_km(*here, *ASOS_STATIONS[159])
        assert d_picked < d_busan

    def test_아주_먼_바다는_None(self):
        """반경(50km) 밖이면 관측소를 고르지 않는다 — 폴백 경로가 살아 있어야 한다."""
        assert ASOSClient.nearest_station(30.0, 140.0) is None
