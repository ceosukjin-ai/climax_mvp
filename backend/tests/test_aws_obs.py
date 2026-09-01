"""AWS 실측(현천·강수감지) — 파서·판정 우선순위 테스트 (2026-09-01)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.aws_obs import (
    StationRain,
    parse_awsh_rain,
    parse_sfc_aws_day,
    is_fog_code,
    is_rain_code,
    num,
    parse_apihub_table,
)

KST = timezone(timedelta(hours=9))


# ===== 헤더 기반 파싱 =====

# 2026-09-01 실제 응답 형식 그대로
AWS_MIN = """#START7777
# YYMMDDHHMI   STN    WD1    WS1    WDS    WSS   WD10   WS10     TA     RE RN-15m RN-60m RN-12H RN-DAY     HM     PA     PS     TD
 202609011532    42  216.6    6.5  212.2    7.1  227.3    5.9   29.6  -99.9    0.0    0.0    0.1    0.1   82.0 1009.1 1012.0   26.2
 202609011532    43  245.5    4.2  254.5    4.5  246.5    3.4   31.2  -99.9    0.0    0.0    0.0    0.0   72.3 1008.7 1011.5   25.6
 202609011532   159  270.0    3.2  270.0    3.5  270.0    3.0   24.5    1.0    0.0    0.0    0.4    0.4   95.0 1008.0 1011.0   23.8
 202609011532   921  180.0    1.0  180.0    1.2  180.0    0.9   -9.5    0.0    0.0    0.0    0.0    0.0   60.0 1008.0 1011.0  -15.2
#7777END
"""

AWS_VIS = """#START7777
# YYMMDDHHMI   STN         LON.         LAT.  S   VIS1  VIS10    WW1   WW15
 202609011532    45 128.11805725  34.91888809  1  50000   -999      0   -999
 202609011532   159 129.03202820  35.10468292  1   3200   -999     53   -999
#7777END
"""

AWS_CLOUD = """#START7777
# YYMMDDHHMI   STN         LON.         LAT. CH_LOW CH_MID CH_TOP CA_TOT
 202609011532    90 128.56472778  38.25085068    851   7620   7620    4.0
 202609011532   159 129.03202820  35.10468292    420   3000   7620    9.0
#7777END
"""


def test_parse_reads_by_column_name():
    rows = parse_apihub_table(AWS_MIN)
    assert len(rows) == 4
    assert rows[0]["STN"] == "42"
    assert rows[0]["RN-15m"] == "0.0"
    assert rows[0]["TD"] == "26.2"


def test_parse_strips_trailing_dot_in_header():
    """실제 응답의 좌표 컬럼명이 'LON.' 'LAT.' 로 온다 — 점을 떼고 읽는다."""
    rows = parse_apihub_table(AWS_VIS)
    assert "LON" in rows[0] and "LAT" in rows[0]
    assert float(rows[1]["LAT"]) == 35.10468292


def test_parse_without_header_returns_nothing():
    """헤더를 못 찾으면 위치를 짐작하지 않는다."""
    assert parse_apihub_table(" 202609011000 159 270 3.2\n") == []


def test_num_handles_missing_codes():
    rec = {"RE": "-99.9", "TA": "24.5", "HM": "-99.0"}
    assert num(rec, "RE") is None
    assert num(rec, "HM") is None
    assert num(rec, "TA") == 24.5
    assert num(rec, "없는컬럼", "TA") == 24.5    # 후보 중 있는 것을 쓴다
    assert num(rec, "없는컬럼") is None


def test_negative_winter_temperature_is_not_missing():
    """−9 이하를 싸잡아 결측 처리하면 겨울 기온이 사라진다."""
    rows = {int(r["STN"]): r for r in parse_apihub_table(AWS_MIN)}
    assert num(rows[921], "TA") == -9.5        # 결측 아님
    assert num(rows[921], "TD") == -15.2
    assert num(rows[42], "RE") is None         # -99.9 는 결측


# ===== 현천 코드 =====

def test_rain_codes():
    for c in (40, 42, 50, 53, 59, 60, 68, 71, 76):
        assert is_rain_code(c), c
    for c in (0, 1, 2, 4, 10, 30, 39, 80):
        assert not is_rain_code(c), c
    assert not is_rain_code(None)


def test_fog_codes():
    assert is_fog_code(30)
    assert not is_fog_code(53)      # 안개비는 비지 안개가 아니다


# ===== 판정 우선순위 — 이게 핵심 =====

def test_drizzle_detected_when_gauge_reads_zero():
    """우량계 0.0mm 인데 1시간 중 15분간 비가 감지된 경우 → 비다.

    전도형 우량계는 물이 일정량 모여야 넘어가므로 이슬비를 0 으로 기록한다.
    RE_SUM 이 그 구멍을 메운다.
    """
    r = StationRain(stn=159, rn_mm=0.0, re_min=15.0)
    assert r.raining is True
    assert r.evidence == "강수감지"


def test_present_weather_beats_everything():
    """현천 센서가 안개비(53)라고 하면 우량계·감지분수보다 우선한다."""
    r = StationRain(stn=159, rn_mm=0.0, re_min=0.0, ww=53)
    assert r.raining is True
    assert r.evidence == "현천"


def test_present_weather_can_also_say_no():
    r = StationRain(stn=159, rn_mm=0.0, re_min=0.0, ww=0)
    assert r.raining is False


def test_gauge_used_when_nothing_better():
    r = StationRain(stn=159, rn_mm=1.4)
    assert r.raining is True
    assert r.evidence == "우량계"


def test_no_data_means_unknown_not_dry():
    """아무 자료도 없으면 '안 온다'가 아니라 '모른다'."""
    r = StationRain(stn=159)
    assert r.raining is None
    assert r.evidence is None


def test_full_parse_to_judgement():
    """실제 형식 세 응답을 파싱해 판정까지.

    159 는 우량계 0.0mm 인데 현천 53(안개비) → 비.
    42 는 강수감지 결측·우량계 0.0 → 우량계로 무강수.
    """
    minute = {int(r["STN"]): r for r in parse_apihub_table(AWS_MIN)}
    vis = {int(r["STN"]): r for r in parse_apihub_table(AWS_VIS)}
    cloud = {int(r["STN"]): r for r in parse_apihub_table(AWS_CLOUD)}

    s159 = StationRain(
        stn=159,
        rn_mm=num(minute[159], "RN-15m"),
        re_flag=num(minute[159], "RE"),
        ta_c=num(minute[159], "TA"), td_c=num(minute[159], "TD"),
        ww=num(vis[159], "WW1"),
        cloud_base_m=num(cloud[159], "CH_LOW"),
    )
    assert s159.rn_mm == 0.0            # 우량계는 0
    assert s159.raining is True         # 그래도 비다
    assert s159.evidence == "현천"
    assert s159.dew_depression_c == 0.7  # 24.5 − 23.8, 매우 습함
    assert s159.cloud_base_m == 420.0

    s42 = StationRain(
        stn=42, rn_mm=num(minute[42], "RN-15m"), re_flag=num(minute[42], "RE"),
        ta_c=num(minute[42], "TA"), td_c=num(minute[42], "TD"),
    )
    assert s42.re_flag is None          # -99.9 = 강수감지기 없음
    assert s42.raining is False
    assert s42.evidence == "우량계"
    assert s42.dew_depression_c == 3.4

    # 강수감지가 1 이면 우량계가 0 이어도 비
    s_detect = StationRain(stn=159, rn_mm=0.0, re_flag=num(minute[159], "RE"))
    assert s_detect.re_flag == 1.0
    assert s_detect.raining is True and s_detect.evidence == "강수감지"


def test_dry_layer_signal():
    """습수가 크면 지상 도달 전 증발 가능성 — 레이더엔 비, 땅은 마름."""
    wet = StationRain(stn=1, ta_c=24.5, td_c=23.8)
    dry = StationRain(stn=2, ta_c=31.2, td_c=15.6)
    assert wet.dew_depression_c < 1.0
    assert dry.dew_depression_c > 15.0
    assert StationRain(stn=3, ta_c=24.0).dew_depression_c is None


# ===== awsh.php?var=RN — 컬럼 이름이 잘려 중복으로 오는 응답 =====

AWSH_RN = """#START7777
# YYMMDDHHMI   STN  RE  RE     RN  MI     RN  MI     RN  MI QCM     RN  MI QCM
 202609011500    42   0  53    0.1   0    0.0 -60    0.0 -59  60    0.0 -59  60
 202609011500    43   0  60    0.0   0    0.0 -60    0.0 -59  60    0.0 -59  60
 202609011500   159  18  60    2.4   0    0.3 -60    0.3 -59  60    0.3 -59  60
 202609011500   777   0   0    0.0   0    0.0 -60    0.0 -59  60    0.0 -59  60
#7777END
"""


def test_awsh_rain_positional_parse():
    """이름이 'RE RE RN MI RN MI...' 로 중복돼 자리로 읽는다.

    자리 해석은 기본 호출 응답과 교차 대조해 확인했다 —
    42번의 RN_DAY=0.1, RN_HR1=0.0 이 두 응답에서 일치.
    """
    rows = parse_awsh_rain(AWSH_RN)
    assert rows[42]["rn_day"] == 0.1
    assert rows[42]["rn_hr1"] == 0.0
    assert rows[42]["re_sum"] == 0.0        # 비 안 옴
    assert rows[159]["re_sum"] == 18.0      # 60분 중 18분간 감지
    assert rows[159]["rn_hr1"] == 0.3


def test_awsh_rain_ignores_when_qc_count_zero():
    """RE_QCM(사용된 자료 수)이 0 이면 RE_SUM 은 의미가 없다 → 모른다."""
    rows = parse_awsh_rain(AWSH_RN)
    assert rows[777]["re_qcm"] == 0.0
    assert rows[777]["re_sum"] is None


def test_awsh_rain_refuses_unexpected_header():
    """헤더 모양이 다르면 자리로 읽지 않는다 — 엉뚱한 칸을 강수량으로 읽느니 포기."""
    other = AWSH_RN.replace("RE  RE     RN  MI     RN  MI", "XX  YY     ZZ  MI     RN  MI")
    assert parse_awsh_rain(other) == {}


def test_re_sum_beats_re_flag():
    """분 단위 감지(RE_SUM)가 유무(RE)보다 우선한다."""
    r = StationRain(stn=159, rn_mm=0.0, re_flag=0.0, re_min=18.0)
    assert r.raining is True and r.evidence == "강수감지"


# ===== sfc_aws_day.php — 헤더에 없는 한글 지점명이 줄 끝에 붙어 온다 =====

SFC_AWS_DAY = """#START7777
#  1. TM     : 관측시각
#  2. STN    : 국내 지점번호
# YYMMDD   STN         LON          LAT       HT      VAL
 20260831    42 126.59737000  35.93681000    2.05    189.3 군산오식도
 20260831    43 126.41163000  34.71083000    1.19      1.0 솔라시도
 20260831   159 129.03203000  35.10468000   69.56      0.0 부산
#7777END
"""


def test_sfc_aws_day_gives_coords_and_names():
    st = parse_sfc_aws_day(SFC_AWS_DAY)
    assert len(st) == 3
    lat, lon, name = st[42]
    assert round(lat, 3) == 35.937 and round(lon, 3) == 126.597
    assert name == "군산오식도"          # 헤더에 없는 7번째 토큰
    assert st[159][2] == "부산"


def test_sfc_aws_day_ignores_comment_lines_that_mention_stn():
    """'#  2. STN : 국내 지점번호' 같은 설명 줄을 헤더로 오인하지 않는다."""
    st = parse_sfc_aws_day(SFC_AWS_DAY)
    assert 2 not in st and len(st) == 3


def test_registry_learns_names_and_coords():
    from app.services.aws_obs import StationRegistry
    import tempfile, os as _os
    fd, path = tempfile.mkstemp(suffix=".json")
    _os.close(fd)
    _os.unlink(path)
    reg = StationRegistry(cache_path=path)
    for stn, (lat, lon, nm) in parse_sfc_aws_day(SFC_AWS_DAY).items():
        reg.learn(stn, lat, lon, nm)
    assert reg.name(42) == "군산오식도"
    assert len(reg.stations) == 3
    reg.flush()

    again = StationRegistry(cache_path=path)
    again.load()
    assert again.name(42) == "군산오식도"      # 캐시에 이름까지 남는다
    _os.unlink(path)


# ===== 강도 구분 — 2026-09-01 해운대 실측으로 정한 경계 =====

def test_intensity_levels():
    from app.services.aws_obs import StationRain as SR
    # 우량계가 넘어갔다 = 물이 모였다 → 비
    assert SR(stn=1, rn_mm=0.3).intensity == "비"
    # 현천이 비라고 하고 60분 중 10분 이상 감지 → 비
    assert SR(stn=1, rn_mm=0.0, re_min=30.0, ww=61).intensity == "비"
    # 누적 감지는 많은데 최근 15분엔 물이 안 모였다 → 그쳤거나 아주 약하다 → 빗방울
    assert SR(stn=1, rn_mm=0.0, re_min=30.0).intensity == "빗방울"
    # 조금 감지 → 빗방울
    assert SR(stn=1, rn_mm=0.0, re_min=4.0).intensity == "빗방울"
    # 현천만 비 (우량계 0, 감지 0) → 빗방울  ← 실측에서 "한두 방울" 이었던 조합
    assert SR(stn=1, rn_mm=0.0, re_min=0.0, ww=61).intensity == "빗방울"
    # 아무 신호도 없음
    assert SR(stn=1, rn_mm=0.0, re_min=0.0, ww=0).intensity == "없음"
    # 판정 근거 자체가 없음
    assert SR(stn=1).intensity is None


def test_real_case_haeundae_20260901():
    """실측 재현 — 13.9km 지점 RN=0.0 · RE_SUM=0 · 현천=비.

    현장 확인: 16시경 "한두 방울", 17시 무강수.
    예전 로직은 이걸 '비'로 판정해 "우산 챙기세요"라고 했다. 이제 '빗방울'이다.
    """
    from app.services.aws_obs import StationRain as SR
    st = SR(stn=983, rn_mm=0.0, re_min=0.0, ww=61)
    assert st.intensity == "빗방울"
    assert st.raining is True          # 신호가 있는 건 맞다
    assert st.evidence == "현천"


def test_gauge_wins_for_positive_reading():
    """현천이 맑음(0)이라 해도 우량계에 물이 모였으면 비다."""
    from app.services.aws_obs import StationRain as SR
    assert SR(stn=1, rn_mm=1.2, ww=0).intensity == "비"


# ===== 자료마다 시간 범위가 다르다 =====

def test_present_weather_clear_beats_hourly_accumulation():
    """현천계가 '지금 맑음'이라고 하면 1시간 누적값보다 그쪽을 믿는다.

    실측(2026-09-01)에서 속초·철원·파주가 RE_SUM>0 인데 WW=0(맑음)이었다.
    누적값만 보면 "지금 비"가 되지만, 그건 50분 전에 그친 비일 수 있다.
    """
    from app.services.aws_obs import StationRain as SR
    st = SR(stn=90, rn_mm=0.0, rn_hour_mm=0.4, re_min=8.0, ww=0.0)
    assert st.intensity == "없음"       # 지금은 안 온다
    assert st.recent_rain is True       # 다만 지난 1시간에는 있었다


def test_hourly_accumulation_alone_is_only_drizzle():
    """현천계가 없는 지점은 누적값뿐이라 '지금'을 단정하지 못한다."""
    from app.services.aws_obs import StationRain as SR
    st = SR(stn=1, rn_mm=0.0, re_min=12.0)
    assert st.intensity == "빗방울"
    assert st.recent_rain is True


def test_fresh_15min_gauge_is_rain():
    from app.services.aws_obs import StationRain as SR
    assert SR(stn=1, rn_mm=0.1, ww=0.0).intensity == "비"   # 15분 안에 물이 모였다


def test_present_weather_rain_with_sustained_detection_is_rain():
    from app.services.aws_obs import StationRain as SR
    assert SR(stn=1, rn_mm=0.0, re_min=30.0, ww=61).intensity == "비"
    assert SR(stn=1, rn_mm=0.0, re_min=0.0, ww=61).intensity == "빗방울"


# ===== AWS 지점별 레이더 합성값 (2026-09-01 실제 응답 형식) =====

RADAR_AWS = """#START7777
#  5. ECHO       :  레이더 에코 값(HSR --> 반사도(dBz), HSP --> 강우강도(mm/h))
#                   ※ -250 : 강수없음, ※ -300 : 관측반경 밖
# YYMMDDHHMI    STN CMP QCD ECHO     HT  STN_KO
 202609011900,   42,HSP,EXT,-250.000, 158.0,군산오식도,,=
 202609011900,   43,HSP,EXT,  0.090, 141.5,솔라시도,,=
 202609011900,   90,HSP,EXT,  0.960,  98.5,속초,,=
 202609011900,  999,HSP,EXT,-300.000,   0.0,먼곳,,=
#7777END
"""


def test_parse_radar_aws():
    from app.services.aws_obs import parse_radar_aws
    r = parse_radar_aws(RADAR_AWS)
    assert r[42]["mmh"] == 0.0          # -250 = 강수없음 (모름이 아니다)
    assert r[43]["mmh"] == 0.09         # mm/h 그대로
    assert r[43]["height_m"] == 141.5
    assert r[43]["name"] == "솔라시도"
    assert r[999]["mmh"] is None        # -300 = 관측 반경 밖 → 모른다


def test_sky_vs_ground():
    """레이더(하늘)와 관측소(땅) 대조 — 기상청 앱도 네이버도 안 하는 부분."""
    from app.services.aws_obs import StationRain as SR

    # 레이더엔 있는데 땅엔 없다 → 떨어지다 증발했거나 아직 안 닿았다
    st = SR(stn=1, rn_mm=0.0, re_min=0.0, ww=0, radar_mmh=1.2, echo_height_m=1800.0)
    assert st.aloft is True
    assert st.intensity == "없음"
    assert st.sky_vs_ground == "하늘만"

    # 둘 다 있다
    st = SR(stn=1, rn_mm=0.5, radar_mmh=1.2)
    assert st.sky_vs_ground == "일치"

    # 레이더는 못 봤는데 땅엔 온다 (약한 비)
    st = SR(stn=1, rn_mm=0.0, re_min=0.0, ww=53, radar_mmh=0.0)
    assert st.intensity == "빗방울"
    assert st.sky_vs_ground == "땅만"

    # 레이더 반경 밖이면 대조 자체를 안 한다
    assert SR(stn=1, rn_mm=0.5, radar_mmh=None).sky_vs_ground is None
