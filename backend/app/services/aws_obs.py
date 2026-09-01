"""방재기상관측(AWS) 실측 — 지상에서 실제로 비가 오는지 확인하는 축.

2026-09-01 작성 → 같은 날 **실제 응답을 받아 형식 확정**.

왜 ASOS 로 부족했나
-------------------
ASOS 97곳은 전국 평균 간격 32km, 부산에 2곳뿐이다. 그리고 결정적으로
**전도형 우량계는 약한 비를 0.0mm 로 기록한다.** 물이 일정량 모여야 한 번 넘어가기
때문에 이슬비·부슬비는 강수량 0 으로 남는다. 사용자는 "비 오는데?" 하는데
관측은 0mm 인 상황이 생긴다.

AWS 가 그 구멍을 메운다 (실측 확인된 것만)
------------------------------------------
* **지점 수** 매분자료 약 800곳
* **RE** — 매분자료의 강수 유무(감지). 강수량이 0.0 이어도 감지되면 값이 선다
* **WW1** — 현천 코드. 시정자료에 함께 온다. 50~59 가 안개비(이슬비)
* **TD** — 이슬점. 습수(TA−TD)가 크면 지상 도달 전 증발 가능성 ↑
* **CH_LOW / CA_TOT** — 하층 운고와 전운량. 운고가 높을수록 떨어지며 증발할 시간이 길다

판정 우선순위
-------------
    ① 현천(WW)  ② 강수감지(RE)  ③ 강수량(RN)  ④ 무강수

앞의 것일수록 약한 비를 잘 잡는다. 셋 다 없으면 "모른다"이지 "안 온다"가 아니다.

실제 응답에서 확인한 것 (2026-09-01)
------------------------------------
* 매분자료 컬럼: ``YYMMDDHHMI STN WD1 WS1 WDS WSS WD10 WS10 TA RE
  RN-15m RN-60m RN-12H RN-DAY HM PA PS TD`` — 공백 구분. **강수량 컬럼명이 `RN` 이
  아니라 `RN-15m` 계열이다.** 좌표는 없다.
* 시정자료: ``YYMMDDHHMI STN LON. LAT. S VIS1 VIS10 WW1 WW15`` — **좌표가 온다.**
* 운고운량: ``YYMMDDHHMI STN LON. LAT. CH_LOW CH_MID CH_TOP CA_TOT`` — **좌표가 온다.**
* 현천자료(ww1)는 쉼표 구분에 컬럼이 가변(코드·횟수 쌍 반복)이라 파싱이 까다롭다.
  같은 WW1 을 시정자료가 주므로 **시정자료를 쓴다.**
* 시간통계(awsh.php)에는 **RE_SUM 이 오지 않았다** — 명세에는 있으나 기본 호출로는
  ``TA WD WS RN_DAY RN_HR1 HM PA PS`` 만 온다. ``var=RN`` 확인 전까지는 쓰지 않는다.
* ``stn_inf?inf=AWS`` 는 404. **좌표는 시정·운고 응답에서 모은다.**

결측 처리
---------
실측에서 ``-9``, ``-99.9``, ``-999`` 가 관찰됐다. 다만 **"-9 이하는 전부 결측"으로
잘라내면 안 된다** — 겨울 기온 −9.5°C 가 결측으로 사라진다. 알려진 결측 코드와
값 범위를 함께 본다.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

KST = timezone(timedelta(hours=9))

APIHUB = "https://apihub.kma.go.kr/api/typ01"
URL_AWS_MIN = f"{APIHUB}/cgi-bin/url/nph-aws2_min"          # 매분자료 (RE·RN·TA·TD)
URL_AWS_VIS = f"{APIHUB}/cgi-bin/url/nph-aws2_min_vis"      # 시정 (+WW1, 좌표)
URL_AWS_CLOUD = f"{APIHUB}/cgi-bin/url/nph-aws2_min_cloud"  # 운고운량 (+좌표)
URL_AWS_DAY = f"{APIHUB}/url/sfc_aws_day.php"               # 일통계 (지점 좌표·이름)
URL_AWS_HOUR = f"{APIHUB}/url/awsh.php"                     # 시간통계 var=RN (RE_SUM)
URL_RDR_AWS = f"{APIHUB}/cgi-bin/url/nph-rdr_cmp_aws_all_pt_data"   # AWS 지점별 레이더값

# 레이더 결측 코드 (실측 확인 2026-09-01)
# 레이더 전 지점 조회는 다른 조회보다 훨씬 느리다(실측 30.7초).
# 기본 30초로는 간발의 차이로 잘려서 값이 하나도 안 들어왔다 (2026-09-01 운영).
RADAR_TIMEOUT_SEC = 75.0

RADAR_NO_ECHO = -250.0      # 강수 없음
RADAR_OUT_OF_RANGE = -300.0  # 관측 반경 밖

# 현천 코드 — 40~42 비, 50~59 안개비, 60~68 비, 71~76 눈
WW_RAIN_RANGES = ((40, 42), (50, 59), (60, 68), (71, 76))
WW_FOG_RANGES = ((30, 39),)

# 실측에서 확인된 결측 코드. 범위 검증과 함께 쓴다.
MISSING_VALUES = {-9.0, -9.9, -99.0, -99.9, -999.0, -999.9, -9999.0}

# 강도 구분 임계 (2026-09-01 해운대 실측 검증으로 정함)
#
# 대표가 현장에서 확인: 16시경 "한두 방울" 수준, 17시엔 무강수.
# 그때 13.9km 지점 값이 RN=0.0 · RE_SUM=0 · 현천=비 였다.
# 즉 **현천만 비라고 하는 상태 = 빗방울**이지 우산 쓸 비가 아니다.
# 앱이 그걸 "지금 비가 옵니다 — 우산 챙기세요"라고 말했던 것이 오판이었다.
#
# 우량계가 넘어갔다(RN>0)는 건 최소 0.5mm 급이 모였다는 뜻이라 '비'로 본다.
# 감지 분수는 60분 중 10분 이상이면 '비', 그 미만이면 '빗방울'.
WEAK_RE_MINUTES = 10.0

def _station_cache_path() -> str:
    """지점 좌표 캐시 위치.

    컨테이너는 비루트(climax)로 돌고 /app 은 root 소유라 거기엔 못 쓴다.
    (2026-09-01 운영 로그: Permission denied: /app/aws_stations.json →
     매 요청마다 714곳을 다시 받아 20초씩 쓰고 있었다)
    쓸 수 있는 곳을 순서대로 고른다.
    """
    env = os.environ.get("AWS_STATION_CACHE")
    if env:
        return env
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for d in (os.path.join(here, ".cache"), here, tempfile.gettempdir()):
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return os.path.join(d, "aws_stations.json")
        except OSError:
            continue
    return os.path.join(tempfile.gettempdir(), "aws_stations.json")


STATION_CACHE = _station_cache_path()
STATION_CACHE_TTL_SEC = 30 * 24 * 3600


def is_rain_code(ww: float | None) -> bool:
    if ww is None:
        return False
    v = int(ww)
    return any(lo <= v <= hi for lo, hi in WW_RAIN_RANGES)


def is_fog_code(ww: float | None) -> bool:
    if ww is None:
        return False
    v = int(ww)
    return any(lo <= v <= hi for lo, hi in WW_FOG_RANGES)


# ---------------------------------------------------------------- 파싱

def parse_apihub_table(text: str) -> list[dict[str, str]]:
    """API허브 typ01 공백구분 응답을 컬럼명 기반으로 판다.

    헤더(컬럼명 줄)를 못 찾으면 **빈 목록**을 돌려준다. 위치를 짐작해서 읽지 않는다 —
    틀린 자리에서 숫자를 읽어 "비 온다"고 하느니 모른다고 하는 편이 낫다.
    """
    header: list[str] | None = None
    rows: list[dict[str, str]] = []

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            toks = [t.rstrip(".") for t in s.lstrip("#").split()]
            if ("STN" in toks and len(toks) >= 3
                    and toks[0].upper().startswith(("YY", "TM"))):
                header = toks
            continue
        if header is None or s.startswith(("=", "7777")):
            continue
        t = s.split()
        if len(t) < len(header):
            continue
        rows.append(dict(zip(header, t)))
    return rows


# 컬럼별 유효 범위. 범위를 벗어나면 결측으로 본다.
RANGES: dict[str, tuple[float, float]] = {
    "TA": (-45.0, 55.0), "TD": (-60.0, 45.0), "HM": (0.0, 100.0),
    "WD1": (0.0, 360.0), "WD10": (0.0, 360.0), "WD": (0.0, 360.0),
    "WS1": (0.0, 80.0), "WS10": (0.0, 80.0), "WS": (0.0, 80.0),
    "RE": (0.0, 1000.0),
    "RN-15m": (0.0, 300.0), "RN-60m": (0.0, 500.0),
    "RN-DAY": (0.0, 2000.0), "RN_HR1": (0.0, 500.0), "RN_DAY": (0.0, 2000.0),
    "WW1": (0.0, 99.0), "WW15": (0.0, 99.0),
    "VIS1": (0.0, 100000.0), "VIS10": (0.0, 100000.0),
    "CH_LOW": (0.0, 30000.0), "CH_MID": (0.0, 30000.0), "CH_TOP": (0.0, 30000.0),
    "CA_TOT": (0.0, 10.0),
    "LON": (120.0, 135.0), "LAT": (30.0, 45.0),
}


def num(rec: dict[str, str], *names: str) -> float | None:
    """후보 컬럼 중 먼저 있는 것을 숫자로. 결측 코드·범위 밖은 None.

    "-9 이하는 결측"으로 뭉뚱그리지 않는다 — 겨울 기온 −9.5°C 가 사라진다.
    """
    for n in names:
        if n not in rec:
            continue
        try:
            v = float(rec[n])
        except (TypeError, ValueError):
            continue
        if v in MISSING_VALUES:
            return None
        lo, hi = RANGES.get(n, (-1e9, 1e9))
        if not (lo <= v <= hi):
            return None
        return v
    return None


# --- awsh.php?var=RN 전용 파서 ---
#
# 이 응답은 **컬럼 이름이 잘려 중복으로 온다.** 실측 헤더:
#
#     # YYMMDDHHMI   STN  RE  RE     RN  MI     RN  MI     RN  MI QCM     RN  MI QCM
#      202609011500    42   0  53    0.1   0    0.0 -60    0.0 -59  60    0.0 -59  60
#
# 이름으로는 구분이 안 되므로 **자리로 읽는다.** 다만 자리로 읽는 건 위험하므로
# 헤더 모양이 정확히 이 형태일 때만 읽고, 조금이라도 다르면 포기한다.
#
# 자리 해석 (명세의 출력 순서 + 기본 호출 응답과 교차 대조해 확인):
#     0 TM · 1 STN · 2 RE_SUM · 3 RE_QCM · 4 RN_DAY · 5 RN_DAY_MI · 6 RN_HR1 · 7 RN_HR1_MI
# 교차 대조: 42번 지점의 RN_DAY=0.1, RN_HR1=0.0 이 기본 호출(var 없음) 응답과 일치.

AWSH_RN_HEADER = ("STN", "RE", "RE", "RN", "MI", "RN", "MI")


def parse_awsh_rain(text: str) -> dict[int, dict[str, float | None]]:
    """awsh.php?var=RN 응답 → {지점: {re_sum, re_qcm, rn_day, rn_hr1}}.

    RE_SUM 은 **과거 60분 중 '강수있음'으로 감지된 분(分)의 수**다.
    강수량이 0.0mm 여도 RE_SUM=15 면 15분간 비가 감지된 것 — 약한 비의 정답.
    RE_QCM(사용된 자료 수)이 0 이면 RE_SUM 은 의미가 없으므로 모른다고 둔다.
    """
    header: list[str] | None = None
    out: dict[int, dict[str, float | None]] = {}

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            toks = s.lstrip("#").split()
            if "STN" in toks and toks[0].upper().startswith(("YY", "TM")):
                header = toks
            continue
        if header is None:
            continue
        # 헤더 모양이 예상과 다르면 자리로 읽지 않는다
        if tuple(header[1:8]) != AWSH_RN_HEADER:
            return {}
        t = s.split()
        if len(t) < 8:
            continue

        def val(i: int) -> float | None:
            try:
                v = float(t[i])
            except (IndexError, ValueError):
                return None
            return None if v in MISSING_VALUES else v

        try:
            stn = int(t[1])
        except ValueError:
            continue
        re_qcm = val(3)
        out[stn] = {
            "re_sum": val(2) if (re_qcm or 0) > 0 else None,
            "re_qcm": re_qcm,
            "rn_day": val(4),
            "rn_hr1": val(6),
        }
    return out


def parse_sfc_aws_day(text: str) -> dict[int, tuple[float, float, str]]:
    """sfc_aws_day.php 응답 → {지점: (위도, 경도, 한글이름)}.

    실측 헤더는 ``YYMMDD STN LON LAT HT VAL`` 인데 **자료 줄 끝에 한글 지점명이
    하나 더 붙어 온다**(헤더에는 없다). AWS 는 지점 이름을 주는 API 가 따로 없으므로
    여기서 같이 주워 둔다. 이름에 공백이 있을 수 있어 6번째 이후를 모두 붙인다.
    """
    header: list[str] | None = None
    out: dict[int, tuple[float, float, str]] = {}

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            toks = [t.rstrip(".") for t in s.lstrip("#").split()]
            if "STN" in toks and toks[0].upper().startswith(("YY", "TM")):
                header = toks
            continue
        if header is None:
            continue
        t = s.split()
        if len(t) < 4:
            continue
        rec = dict(zip(header, t))
        try:
            stn = int(rec["STN"])
        except (KeyError, ValueError):
            continue
        lat, lon = num(rec, "LAT"), num(rec, "LON")
        if lat is None or lon is None:
            continue
        name = " ".join(t[len(header):]).strip() or str(stn)
        out[stn] = (lat, lon, name)
    return out


# --- AWS 지점별 레이더 합성값 ---
#
# 실측 응답(2026-09-01):
#     # YYMMDDHHMI    STN CMP QCD ECHO     HT  STN_KO
#      202609011900,   43,HSP,EXT,  0.090, 141.5,솔라시도,,=
#
# **cmp=HSP 는 ECHO 가 강우강도(mm/h)** 로 온다(HSR 은 반사도 dBZ). 변환이 필요 없다.
# HT 는 레이더 에코 고도(m) — 높을수록 떨어지며 증발할 시간이 길다.
# 헤더는 공백 구분인데 자료는 쉼표 구분이라 자리로 읽는다(끝에 ",,=" 가 붙는다).

def parse_radar_aws(text: str) -> dict[int, dict[str, float | str | None]]:
    """{지점: {mmh, height_m, name}}. 관측 반경 밖(-300)은 None(모름)으로 둔다."""
    out: dict[int, dict[str, float | str | None]] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("7777"):
            continue
        t = [x.strip() for x in s.split(",")]
        if len(t) < 7:
            continue
        try:
            stn = int(t[1])
            echo = float(t[4])
            ht = float(t[5])
        except (ValueError, IndexError):
            continue

        if echo <= RADAR_OUT_OF_RANGE:
            mmh = None                    # 관측 반경 밖 — 모른다
        elif echo <= RADAR_NO_ECHO:
            mmh = 0.0                     # 강수 없음 — 안다
        else:
            mmh = echo
        out[stn] = {
            "mmh": mmh,
            "height_m": ht if ht > 0 else None,
            "name": t[6] or None,
        }
    return out


# ---------------------------------------------------------------- 관측 1건

@dataclass(slots=True)
class StationRain:
    """한 지점의 '지금 비가 오는가' 판정 재료."""

    stn: int
    tm: datetime | None = None
    rn_mm: float | None = None        # 강수량 (RN-15m 우선)
    rn_hour_mm: float | None = None   # 1시간 강수량
    re_flag: float | None = None      # RE — 강수 유무(감지)
    re_min: float | None = None       # RE_SUM — 최근 60분 중 감지된 분 (오면 사용)
    ww: float | None = None           # 현천 코드
    ta_c: float | None = None
    td_c: float | None = None         # 이슬점
    ws_ms: float | None = None
    wd_deg: float | None = None
    cloud_base_m: float | None = None  # 하층 운고
    cloud_tenths: float | None = None
    visibility_m: float | None = None
    radar_mmh: float | None = None      # 레이더 강우강도 (None=반경 밖/모름, 0=강수없음)
    echo_height_m: float | None = None  # 레이더 에코 고도 (높을수록 증발 여지 ↑)

    @property
    def intensity(self) -> str | None:
        """비 / 빗방울 / 없음 / None(모름).

        **자료마다 시간 범위가 다르다**는 것이 핵심이다.

            WW1      순간          — 지금
            RN-15m   15분 누적     — 사실상 지금
            RE_SUM   60분 감지분수 — 지난 1시간 (50분 전일 수도 있다)
            RN-60m   60분 누적     — 지난 1시간

        1시간 누적값으로 "지금"을 말하면 이미 그친 비가 계속 현재로 남는다.
        그래서 **순간 신호(WW1)가 맑다고 하면 누적값보다 그쪽을 믿는다.**

        그리고 사용자에게 중요한 건 "왔냐"가 아니라 "우산이 필요하냐"다.
        현천계만 비라 하고 우량계·감지분수가 0 이면 빗방울이지 비가 아니다
        (2026-09-01 해운대 실측: 그 조합이 실제로 "한두 방울" 이었다).
        """
        # ① 최근 15분에 물이 모였다 — 가장 확실한 '지금 비'
        if (self.rn_mm or 0) > 0:
            return "비"

        # ② 현천계는 순간값이다. 있으면 누적값보다 우선한다 — 맑다고 해도 믿는다.
        if self.ww is not None:
            if not is_rain_code(self.ww):
                return "없음"
            return "비" if (self.re_min or 0) >= WEAK_RE_MINUTES else "빗방울"

        # ③ 현천계가 없는 지점 — 누적값뿐이라 '지금'을 단정하지 못한다
        if (self.re_min or 0) > 0 or (self.rn_hour_mm or 0) > 0:
            return "빗방울"

        if (self.re_flag or 0) > 0:
            return "빗방울"

        if (self.ww is not None or self.re_min is not None
                or self.re_flag is not None or self.rn_mm is not None
                or self.rn_hour_mm is not None):
            return "없음"
        return None

    @property
    def aloft(self) -> bool | None:
        """하늘에 비가 있나. None 이면 레이더 관측 반경 밖(모름)."""
        if self.radar_mmh is None:
            return None
        return self.radar_mmh > 0

    @property
    def sky_vs_ground(self) -> str | None:
        """레이더(하늘)와 관측소(땅)가 같은 말을 하는가.

        일치   / 하늘만 (레이더엔 있는데 땅엔 안 닿음 — 증발·꼬리구름)
        땅만   (레이더가 못 본 약한 비) / None (한쪽을 모름)

        이 대조가 기상청 앱도 네이버도 하지 않는 부분이다.
        """
        sky = self.aloft
        ground = self.intensity
        if sky is None or ground is None:
            return None
        wet = ground in ("비", "빗방울")
        if sky and wet:
            return "일치"
        if sky and not wet:
            return "하늘만"
        if not sky and wet:
            return "땅만"
        return "일치"          # 둘 다 없음

    @property
    def recent_rain(self) -> bool:
        """지난 1시간 안에 비가 있었나 — '지금'과 구분해서 쓴다."""
        return (self.re_min or 0) > 0 or (self.rn_hour_mm or 0) > 0

    @property
    def raining(self) -> bool | None:
        """비 또는 빗방울이면 True. 판정 근거가 없으면 None(모른다)."""
        i = self.intensity
        if i is None:
            return None
        return i in ("비", "빗방울")

    @property
    def evidence(self) -> str | None:
        if (self.rn_mm or 0) > 0 or (self.rn_hour_mm or 0) > 0:
            return "우량계"
        if self.re_min is not None and self.re_min > 0:
            return "강수감지"
        if self.ww is not None:
            return "현천"
        if self.re_min is not None or self.re_flag is not None:
            return "강수감지"
        if self.rn_mm is not None or self.rn_hour_mm is not None:
            return "우량계"
        return None

    @property
    def dew_depression_c(self) -> float | None:
        """습수(기온−이슬점). 클수록 지상 부근이 건조 → 떨어지며 증발할 가능성 ↑."""
        if self.ta_c is None or self.td_c is None:
            return None
        return round(self.ta_c - self.td_c, 1)

    @property
    def foggy(self) -> bool:
        return is_fog_code(self.ww)


# ---------------------------------------------------------------- 지점 좌표

class StationRegistry:
    """AWS 지점 좌표표.

    ``stn_inf?inf=AWS`` 가 404 라서 전용 지점 API 를 못 쓴다. 대신 **시정·운고 응답에
    LON/LAT 이 함께 오므로** 관측을 받을 때마다 좌표를 주워 모으고 파일에 쌓아 둔다.
    쓰면 쓸수록 표가 채워진다. 일통계(sfc_aws_day)로 한 번에 채울 수도 있다.
    """

    def __init__(self, cache_path: str = STATION_CACHE) -> None:
        self.cache_path = cache_path
        self._stations: dict[int, tuple[float, float]] = {}
        self._names: dict[int, str] = {}
        self._dirty = False
        self._loaded = False

    @property
    def stations(self) -> dict[int, tuple[float, float]]:
        return self._stations

    def name(self, stn: int) -> str:
        """한글 지점명. 일통계 응답 끝에 붙어 오는 이름을 우선 쓰고,
        없으면 ASOS 표(지점번호 체계가 겹친다)를, 그것도 없으면 번호를 쓴다."""
        if stn in self._names:
            return self._names[stn]
        from app.services.kma import ASOS_STATION_NAMES
        return ASOS_STATION_NAMES.get(stn, str(stn))

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            st = os.stat(self.cache_path)
            if time.time() - st.st_mtime <= STATION_CACHE_TTL_SEC:
                with open(self.cache_path, encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self._stations[int(k)] = (v[0], v[1])
                    if len(v) > 2 and v[2]:
                        self._names[int(k)] = v[2]
                logger.info("AWS 지점 {}곳 (캐시)", len(self._stations))
        except (OSError, ValueError, TypeError) as e:
            logger.debug("AWS 지점 캐시 없음/불량: {}", e)

    def learn(self, stn: int, lat: float | None, lon: float | None,
              name: str | None = None) -> None:
        if name and stn not in self._names and not name.isdigit():
            self._names[stn] = name
            self._dirty = True
        if lat is None or lon is None or stn in self._stations:
            return
        self._stations[stn] = (lat, lon)
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({
                    str(k): [v[0], v[1], self._names.get(k, "")]
                    for k, v in self._stations.items()
                }, f, ensure_ascii=False)
            self._dirty = False
        except OSError as e:
            logger.warning("AWS 지점 캐시 저장 실패: {}", e)


# ---------------------------------------------------------------- 클라이언트

class AWSObsClient:
    """AWS 실측 조회. 전 지점을 한 번의 호출로 훑는 것이 핵심.

    ``stn=0``(전체지점) 조회는 **최대 10분 구간**만 허용된다(1개 지점이면 하루).
    """

    def __init__(self, auth_key: str, timeout_sec: float = 30.0) -> None:
        if not auth_key:
            raise ValueError("API허브 인증키가 필요합니다")
        self.auth_key = auth_key
        self._client = httpx.AsyncClient(timeout=timeout_sec)
        self.registry = StationRegistry()
        self.registry.load()

    async def close(self) -> None:
        self.registry.flush()
        await self._client.aclose()

    async def _get(self, url: str, params: dict,
                   timeout: float | None = None) -> str | None:
        p = dict(params)
        p["authKey"] = self.auth_key
        try:
            resp = await self._client.get(
                url, params=p,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            # 타임아웃은 str(e) 가 비어 있어 로그만 보면 원인을 못 찾는다
            logger.warning("AWS 조회 실패 {}: {}: {}",
                           url.rsplit("/", 1)[-1], type(e).__name__, e or "(메시지 없음)")
            return None
        text = resp.content.decode("euc-kr", errors="replace")
        if '"status"' in text and ('"403"' in text or "403," in text or "404" in text):
            logger.warning("AWS 접근 거부/미제공: {}", url.rsplit("/", 1)[-1])
            return None
        return text

    @staticmethod
    def _tm(rec: dict[str, str]) -> datetime | None:
        for k in ("YYMMDDHHMI", "TM", "KST"):
            v = rec.get(k)
            if v and len(v) >= 12 and v[:12].isdigit():
                return datetime.strptime(v[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
        return None

    def _window(self, minutes: int = 10) -> tuple[str, str]:
        """자료 생산 지연을 감안해 조금 뒤로 물러난 10분 창."""
        now = datetime.now(KST).replace(second=0, microsecond=0) - timedelta(minutes=2)
        return ((now - timedelta(minutes=max(1, min(10, minutes)))).strftime("%Y%m%d%H%M"),
                now.strftime("%Y%m%d%H%M"))

    async def _table(self, url: str, minutes: int = 10) -> list[dict[str, str]]:
        t1, t2 = self._window(minutes)
        text = await self._get(url, {
            "tm1": t1, "tm2": t2, "stn": "0", "disp": "0", "help": "1",
        })
        return parse_apihub_table(text) if text else []

    async def minute_all(self) -> dict[int, StationRain]:
        """매분자료 — 강수감지(RE)·강수량·기온·이슬점·바람. 지점당 최신 1분만 남긴다."""
        out: dict[int, StationRain] = {}
        for rec in await self._table(URL_AWS_MIN):
            try:
                stn = int(rec["STN"])
            except (KeyError, ValueError):
                continue
            tm = self._tm(rec)
            cur = out.get(stn)
            if cur is not None and cur.tm and tm and tm <= cur.tm:
                continue
            out[stn] = StationRain(
                stn=stn, tm=tm,
                rn_mm=num(rec, "RN-15m", "RN-60m"),
                rn_hour_mm=num(rec, "RN-60m"),
                re_flag=num(rec, "RE"),
                ta_c=num(rec, "TA"), td_c=num(rec, "TD"),
                ws_ms=num(rec, "WS1", "WS10"), wd_deg=num(rec, "WD1", "WD10"),
            )
        return out

    async def visibility_all(self) -> dict[int, StationRain]:
        """시정자료 — 현천 코드(WW1)와 좌표가 함께 온다. 현천은 여기서 받는다."""
        out: dict[int, StationRain] = {}
        for rec in await self._table(URL_AWS_VIS):
            try:
                stn = int(rec["STN"])
            except (KeyError, ValueError):
                continue
            self.registry.learn(stn, num(rec, "LAT"), num(rec, "LON"))
            tm = self._tm(rec)
            cur = out.get(stn)
            if cur is not None and cur.tm and tm and tm <= cur.tm:
                continue
            out[stn] = StationRain(
                stn=stn, tm=tm,
                ww=num(rec, "WW1", "WW15"),
                visibility_m=num(rec, "VIS1", "VIS10"),
            )
        return out

    async def cloud_all(self) -> dict[int, StationRain]:
        """운고운량 — 증발 판정(운고)과 체감 엔진(전운량)에 쓴다."""
        out: dict[int, StationRain] = {}
        for rec in await self._table(URL_AWS_CLOUD):
            try:
                stn = int(rec["STN"])
            except (KeyError, ValueError):
                continue
            self.registry.learn(stn, num(rec, "LAT"), num(rec, "LON"))
            tm = self._tm(rec)
            cur = out.get(stn)
            if cur is not None and cur.tm and tm and tm <= cur.tm:
                continue
            out[stn] = StationRain(
                stn=stn, tm=tm,
                cloud_base_m=num(rec, "CH_LOW"),
                cloud_tenths=num(rec, "CA_TOT"),
            )
        return out

    async def hourly_rain_all(self) -> dict[int, dict[str, float | None]]:
        """awsh.php?var=RN — RE_SUM(최근 60분 중 강수 감지된 분 수)을 받는다.

        매분자료의 RE(유무)보다 한 단계 정밀하다. 발표 지연을 감안해 직전 정시부터 본다.
        """
        now = datetime.now(KST)
        for back in (1, 2):
            tm = (now.replace(minute=0, second=0, microsecond=0)
                  - timedelta(hours=back))
            text = await self._get(URL_AWS_HOUR, {
                "var": "RN", "tm": tm.strftime("%Y%m%d%H%M"), "stn": "0", "help": "1",
            })
            if not text:
                continue
            rows = parse_awsh_rain(text)
            if rows:
                return rows
        return {}

    async def radar_all(self) -> dict[int, dict]:
        """AWS 전 지점의 레이더 합성값 (cmp=HSP → 강우강도 mm/h).

        격자(2305x2881, 한 장 13MB)를 5분마다 받는 대신 **지점 값만** 받는다.
        지점 번호가 지상 관측과 같으므로 하늘과 땅을 바로 대조할 수 있다.
        레이더는 5분 주기이고 생산에 6분쯤 걸린다 — 여유를 두고 뒤로 물러난다.
        """
        now = datetime.now(KST)
        for back in (10, 15, 20, 25):
            t = now - timedelta(minutes=back)
            t = t.replace(minute=(t.minute // 5) * 5, second=0, microsecond=0)
            text = await self._get(URL_RDR_AWS, {
                "tm": t.strftime("%Y%m%d%H%M"), "qcd": "EXT", "cmp": "HSP", "help": "1",
            }, timeout=RADAR_TIMEOUT_SEC)
            if not text:
                continue
            rows = parse_radar_aws(text)
            if rows:
                for stn, r in rows.items():
                    self.registry.learn(stn, None, None, r.get("name"))
                return rows
        return {}

    async def refresh_station_coords(self) -> int:
        """일통계로 지점 좌표표를 한 번에 채운다(선택). 실패해도 무해."""
        y = (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")
        text = await self._get(URL_AWS_DAY, {
            "tm2": y, "obs": "rn_day", "stn": "0", "disp": "0", "help": "1",
        })
        if not text:
            return 0
        before = len(self.registry.stations)
        for stn, (lat, lon, nm) in parse_sfc_aws_day(text).items():
            self.registry.learn(stn, lat, lon, nm)
        self.registry.flush()
        gained = len(self.registry.stations) - before
        logger.info("AWS 지점 좌표 {}곳 확보 (+{})", len(self.registry.stations), gained)
        return gained

    async def ensure_stations(self) -> bool:
        """좌표표가 비어 있으면 일통계로 한 번 채워 본다."""
        if self.registry.stations:
            return True
        await self.refresh_station_coords()
        return bool(self.registry.stations)

    @staticmethod
    def merge_parts(
        minute: dict[int, StationRain],
        hourly: dict[int, dict[str, float | None]],
        vis: dict[int, StationRain],
        cloud: dict[int, StationRain],
        radar: dict[int, dict] | None = None,
    ) -> dict[int, StationRain]:
        """네 자료를 지점별로 합친다. 하나가 비어도 나머지로 답한다."""
        merged: dict[int, StationRain] = dict(minute)

        # RE_SUM (분 단위 감지) — 매분자료의 RE(유무)보다 정밀하다
        for stn, rn in hourly.items():
            base = merged.setdefault(stn, StationRain(stn=stn))
            base.re_min = rn.get("re_sum")
            if base.rn_hour_mm is None:
                base.rn_hour_mm = rn.get("rn_hr1")

        for src in (vis, cloud):
            for stn, extra in src.items():
                base = merged.get(stn)
                if base is None:
                    merged[stn] = extra
                    continue
                if extra.ww is not None:
                    base.ww = extra.ww
                if extra.visibility_m is not None:
                    base.visibility_m = extra.visibility_m
                if extra.cloud_base_m is not None:
                    base.cloud_base_m = extra.cloud_base_m
                if extra.cloud_tenths is not None:
                    base.cloud_tenths = extra.cloud_tenths
                if base.tm is None:
                    base.tm = extra.tm

        for stn, r in (radar or {}).items():
            base = merged.setdefault(stn, StationRain(stn=stn))
            base.radar_mmh = r.get("mmh")
            base.echo_height_m = r.get("height_m")
        return merged

    async def observe(self) -> dict[int, StationRain]:
        """지점별 판정 재료. 매분자료를 뼈대로 RE_SUM·현천·운고를 덧입힌다."""
        merged = self.merge_parts(
            await self.minute_all(),
            await self.hourly_rain_all(),
            await self.visibility_all(),
            await self.cloud_all(),
            await self.radar_all(),
        )
        self.registry.flush()
        return merged
