"""개 기준 산책 코스 추천 — 도로망 위에서 가장 시원한 순환 코스를 찾는다.

왜 서버로 옮겼나 (2026-08-25):
    코스 계산은 도로망 타일을 받아 다익스트라를 도는 무거운 일이다. 폰에서 하면
    배터리를 먹고 느리며, **iOS(Swift)와 안드로이드(Kotlin)에 같은 로직을 두 벌**
    유지해야 한다. 서버가 계산해 결과만 내려주면 두 앱이 같은 답을 보고,
    알고리즘을 고칠 때 **앱 심사 없이 당일 반영**된다.

원본: iOS DogRoadGraph.swift / DogCourseFinder.swift / PawBurnEngine.swift /
      DogMrt.swift / DogRiskEngine.swift 를 그대로 옮긴 것.
      **수식을 바꾸지 않았다** — 바꾸면 앱 화면과 서버 답이 어긋난다.

⚠️ 점수 일관성 원칙
    구간 비용 = WBGT(개 높이) + 취약도오프셋 + max(0, 노면온도 − 44)
    시간대 화면(WalkWindow)과 **같은 식**이다. 두 화면이 다른 말을 하면 안 된다.

⚠️ A* 가 아니라 다익스트라인 이유
    비용이 거리만이 아니라 열까지 섞인 값이라 직선거리 휴리스틱이 실제 비용을
    넘어설 수 있다(비허용 휴리스틱 → 최적해를 놓친다). 정확도를 속도와 바꾸지 않는다.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

# ── 물리 상수 (PawBurnEngine 과 동일) ──────────────────────────
SIGMA = 5.670374419e-8
EMISSIVITY = 0.9
ATMOS_EMISSIVITY = 0.70
CONDUCTION_DEPTH_M = 0.0508
DEPTH_RATIO_2IN = 0.8988
DAMAGE_THRESHOLD_C = 44.0

# 노면 물성: (albedo, conductivity, offsetC, air_tracking, 한국어)
SURFACES: dict[str, tuple[float, float, float, bool, str]] = {
    "asphaltNew": (0.07, 1.38, 0.0, False, "아스팔트(신설·검정)"),
    "asphalt":    (0.12, 1.38, 0.0, False, "아스팔트(일반)"),
    "paver":      (0.30, 1.30, 0.0, False, "보도블록·벽돌"),
    "concrete":   (0.35, 1.70, 0.0, False, "콘크리트"),
    "sand":       (0.45, 1.00, 0.0, False, "모래·흙"),
    "grass":      (0.25, 1.00, 3.0, True,  "천연잔디"),
    "turf":       (0.10, 0.80, 4.4, False, "인조잔디"),
}

WALKABLE = {
    "footway", "path", "pedestrian", "steps", "living_street", "residential",
    "service", "unclassified", "tertiary", "tertiary_link", "secondary",
    "secondary_link", "primary", "primary_link", "track", "cycleway",
}

_SURFACE_TAG = {
    "asphalt": "asphalt",
    "concrete": "concrete", "concrete:plates": "concrete", "concrete:lanes": "concrete",
    "paving_stones": "paver", "sett": "paver", "cobblestone": "paver",
    "bricks": "paver", "paved": "paver",
    "sand": "sand",
    "grass": "grass", "grass_paver": "grass", "meadow": "grass",
    "artificial_turf": "turf", "tartan": "turf", "rubber": "turf",
    # 흙길 ≈ 모래 계열
    "ground": "sand", "dirt": "sand", "earth": "sand", "compacted": "sand",
    "fine_gravel": "sand", "gravel": "sand", "unpaved": "sand",
    "wood": "sand", "woodchips": "sand", "mud": "sand",
}

_GREEN_LEISURE = {"park", "garden", "playground", "recreation_ground", "nature_reserve"}
_GREEN_LANDUSE = {"grass", "forest", "meadow", "recreation_ground", "village_green"}
_GREEN_NATURAL = {"wood", "scrub", "grassland", "heath"}


def surface_guess(highway: str) -> str:
    """태그가 없을 때 도로 유형으로 추정하는 노면. **추정이다.**"""
    if highway in ("path", "track"):
        return "sand"
    if highway in ("footway", "pedestrian", "steps", "cycleway"):
        return "paver"
    return "asphalt"


# ── 물리 엔진 ────────────────────────────────────────────────
def surface_temp_c(air_c: float, ghi: float, wind_ms: float,
                   surface: str, shaded: bool, wet: bool) -> float:
    """표면 에너지수지로 노면온도를 푼다 (PawBurnEngine.surfaceTempC 이식)."""
    albedo, cond_k, offset, air_tracking, _ = SURFACES.get(surface, SURFACES["asphalt"])

    if air_tracking:
        # 잔디: 증산으로 기온을 따라간다.
        f = 0.0 if shaded else min(max(ghi / 900.0, 0.0), 1.0)
        t = air_c + 6.0 * f - 1.0
        return min(t, air_c) if wet else t

    g = ghi * 0.15 if shaded else ghi
    absorbed = (1.0 - albedo) * g
    t_air_k = air_c + 273.15
    atmos = ATMOS_EMISSIVITY * SIGMA * t_air_k ** 4

    def net(ts_c: float) -> float:
        ts_k = ts_c + 273.15
        d_t = ts_c - air_c
        tm_k = (ts_k + t_air_k) / 2.0
        forced = 0.00144 * tm_k ** 0.3 * max(wind_ms, 0.1) ** 0.7
        free = 0.00097 * abs(d_t) ** 0.3
        convection = 698.24 * (forced + free) * d_t
        # ⚠️ 원논문의 깊이 프로파일은 **화씨 절대온도** 비율로 캘리브레이션됐다.
        #    그대로 재현해야 논문의 검증 성능(R²=0.82)이 유지된다 — 섭씨로 바꾸지 말 것.
        ts_f = ts_c * 9.0 / 5.0 + 32.0
        sub_c = ts_c - (ts_f * (1.0 - DEPTH_RATIO_2IN)) * 5.0 / 9.0
        conduction = cond_k * (ts_c - sub_c) / CONDUCTION_DEPTH_M
        radiated = EMISSIVITY * SIGMA * ts_k ** 4
        return absorbed + atmos - convection - conduction - radiated

    lo, hi = air_c - 20.0, air_c + 60.0
    for _ in range(80):                     # net 은 Ts 에 대해 단조감소 — 이분법 안전
        mid = (lo + hi) / 2.0
        if net(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0 + offset


def ground_view_factor(withers_cm: float) -> float:
    """체고(cm) → 개의 지면 형태계수. 낮을수록 노면이 시야를 더 채운다."""
    h = min(max(withers_cm, 15.0), 90.0)
    v = 0.60 - (h - 15.0) * (0.60 - 0.36) / (90.0 - 15.0)
    return min(max(v, 0.30), 0.62)


def mrt_at_dog_height(mrt_human_c: float, surface_c: float, withers_cm: float) -> float:
    k4 = lambda c: (c + 273.15) ** 4
    ts4, mrt_h4 = k4(surface_c), k4(mrt_human_c)
    fg_h, fg_d = 0.25, ground_view_factor(withers_cm)
    other4 = max((mrt_h4 - fg_h * ts4) / (1.0 - fg_h), 1.0)
    return (fg_d * ts4 + (1.0 - fg_d) * other4) ** 0.25 - 273.15


def wet_bulb_stull(t_c: float, rh: float) -> float:
    """Stull R (2011) J Appl Meteorol Climatol 50:2267-2269. 오차 약 ±0.65°C."""
    r = min(max(rh, 1.0), 100.0)
    return (t_c * math.atan(0.151977 * (r + 8.313659) ** 0.5)
            + math.atan(t_c + r) - math.atan(r - 1.676331)
            + 0.00391838 * r ** 1.5 * math.atan(0.023101 * r) - 4.686035)


def globe_temp_from_mrt(mrt_c: float, tdb_c: float, wind_ms: float) -> float:
    d, eps, v = 0.15, 0.95, max(wind_ms, 0.05)

    def mrt_of(tg: float) -> float:
        a = (tg + 273.0) ** 4 + 1.1e8 * v ** 0.6 / (eps * d ** 0.4) * (tg - tdb_c)
        return max(a, 1.0) ** 0.25 - 273.0

    lo, hi = min(tdb_c, mrt_c) - 30.0, max(tdb_c, mrt_c) + 60.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if mrt_of(mid) < mrt_c:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def wbgt_outdoor(tdb_c: float, rh: float, wind_ms: float, mrt_c: float) -> float:
    tg = globe_temp_from_mrt(mrt_c, tdb_c, wind_ms)
    tw = wet_bulb_stull(tdb_c, rh)
    margin = 1.0 if wind_ms < 1.0 else 0.0     # 무풍 안전여유
    return 0.7 * tw + 0.2 * tg + 0.1 * tdb_c + margin


@dataclass
class Conditions:
    air_c: float
    ghi: float
    wind_ms: float
    rh: float
    rain: bool = False
    withers_cm: float = 45.0
    vuln_offset_c: float = 0.0
    # ── 자전거 (2026-09-16) ──────────────────────────────────────────────
    # 일본은 자전거 분담률이 높다(도시 통행의 15% 안팎, 역까지 자전거 + 전철이 일상).
    # 자전거가 보행과 물리적으로 다른 점은 둘이다.
    #   ① **속도가 곧 바람이다.** 시속 15 km = 4.2 m/s 의 맞바람이 생긴다.
    #      WBGT·PET·노면 대류가 전부 풍속을 쓰므로 여기만 고치면 세 곳이 같이 맞는다.
    #   ② **대사량이 다르다.** 보행 2.0 MET, 평지 자전거 4~5 MET. PET 입력값이다.
    # 눈높이는 따로 안 바꾼다 — 자전거 탄 사람 눈높이가 보행자와 비슷하다(약 1.4 m).
    # 개(`withers_cm`)가 특별했던 건 달궈진 노면 복사와 발 화상 때문이고, 자전거는 아니다.
    mode: str = "walk"              # "walk" | "bike"
    speed_kmh: float = 0.0          # 0 이면 mode 기본값(BIKE_SPEED_KMH / WALK_SPEED_KMH)
    met: float | None = None        # None 이면 mode 기본값

    def rider_ms(self) -> float:
        """주행으로 생기는 맞바람 [m/s]. 보행은 0.4 m/s 남짓이라 무시한다."""
        if self.mode != "bike":
            return 0.0
        return (self.speed_kmh or BIKE_SPEED_KMH) * 1000.0 / 3600.0

    def eff_wind_ms(self) -> float:
        """체감에 실제로 작용하는 풍속. 주행 맞바람과 기상 풍속을 합친다.

        방향을 모르므로 **제곱합 평균**으로 둔다 — 맞바람일 때와 뒷바람일 때의 중간이다.
        단순 덧셈은 뒷바람 구간을 과대평가하고, 무시하면 자전거의 냉각을 통째로 놓친다.
        """
        r = self.rider_ms()
        if r <= 0.0:
            return self.wind_ms
        return math.hypot(self.wind_ms, r)

    def met_value(self) -> float:
        if self.met is not None:
            return float(self.met)
        return BIKE_MET if self.mode == "bike" else 2.0


def _underground(tags: dict) -> bool:
    """지하 통로인가 (2026-09-18) — 지하가·역 구내 자유통로·지하도.

    판정: layer/level 이 음수이거나 indoor=yes. 도쿄·오사카 지하가는 OSM 에 이렇게 들어 있다.
    지하는 **그늘보다 시원하다** — 그늘은 확산일사 15% 가 남지만 지하는 0 이다.
    냉방은 가정하지 않는다(지하가는 보통 냉방되지만 근거가 없다). 기온은 지상과 같게 둔다.
    """
    if tags.get("indoor") in ("yes", "corridor", "room"):
        return True
    for k in ("layer", "level"):
        v = tags.get(k)
        if v is None:
            continue
        # "-1", "-2", "-1;0"(복층) 등. 첫 값만 보고 음수면 지하로 본다.
        try:
            if float(str(v).split(";")[0].strip()) < 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def edge_cost(surface: str, shaded: bool, c: Conditions,
              underground: bool = False) -> tuple[float, float, float]:
    """한 구간의 비용. **WalkWindow 와 같은 식**이다.

    사람 기준 MRT(`mrt_h`)도 함께 돌려준다 (2026-09-12). 지금까지 여기서 계산해 놓고
    강아지 높이로 변환한 뒤 버렸는데, 경로 위에 **구간별 체감기후**를 그리려면 이 값이 필요하다.
    주의: 이 MRT 는 `기온 + 일사/900×12` 간이식이다. 점 조회에 쓰는 VPTI 정식 경로
    (건물 레이캐스트·재질·열관성·가로수)와 다르므로 절대값은 ±2~3℃ 어긋날 수 있다.
    구간 사이의 **상대 비교**가 목적이며, UI 에서도 개략치임을 밝힌다.
    """
    # 자전거면 주행 맞바람이 더해진다 (2026-09-16). 노면 대류·WBGT 가 함께 바뀐다.
    _wind = max(c.eff_wind_ms(), 0.3)
    # 지하는 일사가 아예 없다 (2026-09-18). 그늘(15%)과 구분한다.
    _ghi = 0.0 if underground else c.ghi
    ts = surface_temp_c(c.air_c, _ghi, _wind, surface, shaded or underground, c.rain)
    ghi_mrt = 0.0 if underground else (c.ghi * 0.15 if shaded else c.ghi)
    mrt_h = c.air_c + ghi_mrt / 900.0 * 12.0
    # 자전거는 사람 높이 그대로 — 개처럼 노면 쪽으로 내리지 않는다.
    mrt_d = mrt_h if c.mode == "bike" else mrt_at_dog_height(mrt_h, ts, c.withers_cm)
    w = wbgt_outdoor(c.air_c, c.rh, _wind, mrt_d)
    return w + c.vuln_offset_c + max(0.0, ts - DAMAGE_THRESHOLD_C), ts, mrt_h


# ── 그래프 ───────────────────────────────────────────────────
def _haversine(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


@dataclass
class Graph:
    coords: list[tuple[float, float]] = field(default_factory=list)
    adj: list[list[tuple[int, float, float, float, bool, bool, float, str, str]]] = field(default_factory=list)
    # adj[i] = [(to, meters, cost, surface_temp, shaded, surface_known, mrt_human, why, surface), ...]
    #   why = sun | bldg(건물그늘) | tree(가로수길) | green(공원·녹지) | covered(터널·지붕) | under(지하)
    cond: Any = None          # 구간 PET 산출에 쓴다 (2026-09-12)
    edge_count: int = 0
    skyline_shaded_edges: int = 0   # 스카이라인 격자로 그늘 판정된 간선 수 (2026-09-11)


def edge_midpoints(elements: Iterable[dict[str, Any]]) -> list[tuple[float, float]]:
    """보행 가능한 간선들의 중점 좌표 — 스카이라인 격자 일괄 조회용 (2026-09-11)."""
    out = []
    for el in elements:
        tags = el.get("tags") or {}
        geom = el.get("geometry") or []
        if tags.get("highway") not in WALKABLE or len(geom) < 2:
            continue
        for i in range(1, len(geom)):
            p, q = geom[i - 1], geom[i]
            if "lat" in p and "lat" in q:
                out.append(((p["lat"] + q["lat"]) / 2, (p["lon"] + q["lon"]) / 2))
    return out


# ── 자전거 통행 가능 (2026-09-16) ──────────────────────────────────────────
# 계단을 남겨 두면 경로가 **거짓**이 된다 — 지도에는 선이 그려지는데 실제로는 못 간다.
# OSM 의 `bicycle=*` 를 우선 보고, 없으면 도로 유형으로 판정한다.
#   · `steps` 는 `bicycle=yes` 가 명시된 경우만 통과(끌고 오르는 계단로는 드물게 표기된다)
#   · `footway`/`pedestrian` 는 일본에서 보통 자전거 통행이 허용되지만(自転車通行可)
#     태그가 없으면 알 수 없다 → **막지 않고 통과시키되 우대하지 않는다.**
#     여기서 막으면 도심 경로가 통째로 끊긴다. 과소차단보다 과대차단이 더 나쁘다.
#   · `motorway`/`trunk` 류는 애초에 WALKABLE 에 없다.
_BIKE_NO = ("no", "dismount", "private")


def _bike_ok(hw: str, tags: dict) -> bool:
    b = str(tags.get("bicycle", "")).lower()
    if b in _BIKE_NO:
        return False
    if b in ("yes", "designated", "permissive"):
        return True
    if hw == "steps":
        return False                    # 태그 없는 계단은 못 간다
    return True


def _bike_class(hw: str, tags: dict) -> str:
    """자전거 관점의 길 종류. **경로 비용에는 쓰지 않는다** — 화면 표시용이다.

    ⚠️ 2026-09-16 정정:
      처음에는 이 함수가 비용 배수(자전거도로 ×0.85, 보도 ×1.15)를 돌려줬고, 그 값을
      `edge_cost` 결과에 곱했다. **그 숫자들은 근거가 없었다 — 내가 감으로 정한 값이다.**

      실제로 뒤집혔다. 도쿄 신주쿠→시부야 실측:
          걷기   그늘 5.4%   자전거 3.4%   (최단 경로 3.5% 보다도 낮다)
      맞바람이 WBGT 를 낮추면서 그늘 간 비용 차이가 줄었는데, 임의 배수는 그대로라
      **근거 없는 값이 물리 계산을 눌러버렸다.**

      -> 경로 결정에는 **물리만** 쓴다(맞바람·대사량·그늘·노면온도). 전부 설명 가능한 값이다.
         자전거도로 여부는 여기서 분류해 `bike_lane_ratio` 로 **표시만** 한다.
         안전을 얼마나 중시할지는 사용자가 정한다.

      자전거 경로 선택 연구(route choice)에는 자전거도로·경사·교통량 가중치를 실측으로
      추정한 값들이 있다. 그걸 가져올 수 있게 되면 그때 비용에 넣는다. 그 전까지는 넣지 않는다.
    """
    b = str(tags.get("bicycle", "")).lower()
    if hw == "cycleway" or b == "designated":
        return "lane"                   # 자전거 전용·지정
    if hw in ("footway", "pedestrian"):
        return "sidewalk"               # 보도 — 갈 수는 있지만 사람과 섞인다
    return "road"


def build_graph(elements: Iterable[dict[str, Any]], cond: Conditions,
                skyline: dict | None = None, sun: tuple[float, float] | None = None) -> Graph:
    """Overpass 형식 elements → 그래프.

    skyline/sun (2026-09-11): 스카이라인 격자(`app.services.skyline.get_cells`)와 태양(방위, 고도)을
    주면 간선 중점 격자의 지평선으로 **건물 그늘**을 판정한다(OSM 태그 covered/tree_lined/녹지에 더해).
    골목·건물 그늘이 OSM 에는 없어서 지금까지 코스가 큰길 그늘만 알았던 것을 바로잡는다.
    격자가 없는 간선은 기존 방식 그대로(폴백).

    좌표를 1e-6 도(약 0.1m)로 반올림해 노드를 합친다 — `nodes` 배열이 없어도
    좌표만으로 길이 이어지게 하기 위해서다(앱과 같은 방식).
    """
    els = list(elements)
    g = Graph()
    index: dict[str, int] = {}
    green_boxes: list[tuple[float, float, float, float]] = []

    # ① 녹지·수변 폴리곤 경계상자 (안에 들어가면 그늘 확률↑)
    for el in els:
        tags = el.get("tags") or {}
        geom = el.get("geometry") or []
        if len(geom) <= 2:
            continue
        if not (tags.get("leisure") in _GREEN_LEISURE
                or tags.get("landuse") in _GREEN_LANDUSE
                or tags.get("natural") in _GREEN_NATURAL):
            continue
        lats = [p["lat"] for p in geom if "lat" in p]
        lons = [p["lon"] for p in geom if "lon" in p]
        if lats and lons and max(lats) > min(lats):
            green_boxes.append((min(lats), min(lons), max(lats), max(lons)))

    def in_green(la: float, lo: float) -> bool:
        return any(b[0] <= la <= b[2] and b[1] <= lo <= b[3] for b in green_boxes)

    def node(la: float, lo: float) -> int:
        key = f"{la:.6f},{lo:.6f}"
        i = index.get(key)
        if i is not None:
            return i
        i = len(g.coords)
        index[key] = i
        g.coords.append((la, lo))
        g.adj.append([])
        return i

    # ② 보행(또는 자전거) 가능한 way 를 간선으로
    _bike = cond is not None and cond.mode == "bike"
    for el in els:
        tags = el.get("tags") or {}
        hw = tags.get("highway")
        geom = el.get("geometry") or []
        if hw not in WALKABLE or len(geom) < 2:
            continue
        if tags.get("area") == "yes" or tags.get("access") == "private" or tags.get("foot") == "no":
            continue
        if _bike and not _bike_ok(hw, tags):
            continue

        tagged = _SURFACE_TAG.get(str(tags.get("surface", "")).lower())
        surface = tagged or surface_guess(hw)
        # 자전거 구간 종류는 **way 단위로 한 번만** 만든다 (2026-09-16 사고).
        # 처음엔 안쪽 구간 반복문 안에서 `surface = f"{surface}|..."` 로 덧붙였는데,
        # 한 way 에 구간이 10개면 `asphalt|road|road|road...` 로 계속 길어지고
        # 다음 반복의 `edge_cost(surface, ...)` 가 알 수 없는 재질을 받아 터졌다.
        # 비용 계산에는 **순수 재질**(`surface`)을, 간선에 싣는 값에는 종류를 붙인 문자열을 쓴다.
        surf_out = f"{surface}|{_bike_class(hw, tags)}" if _bike else surface
        covered = tags.get("covered") == "yes" or (tags.get("tunnel") not in (None, "no"))
        under = _underground(tags)
        tree_lined = tags.get("tree_lined") == "yes"

        for i in range(1, len(geom)):
            p, q = geom[i - 1], geom[i]
            if "lat" not in p or "lat" not in q:
                continue
            d = _haversine(p["lat"], p["lon"], q["lat"], q["lon"])
            if d < 0.5:
                continue
            mla, mlo = (p["lat"] + q["lat"]) / 2, (p["lon"] + q["lon"]) / 2
            # 왜 그늘인지를 함께 남긴다 (2026-09-13). 사용자가 "왜 여기가 초록이냐"고 물었을 때
            # 답할 수 있어야 한다 — 판정하면서 이미 알고 있는 정보인데 버리고 있었다.
            why = "sun"
            if under:
                why = "under"            # 지하가·역 구내 통로 — 일사 0
            elif covered:
                why = "covered"          # 터널·지붕·아케이드
            elif tree_lined:
                why = "tree"             # OSM tree_lined=yes (가로수길로 태그된 길)
            elif in_green(mla, mlo):
                why = "green"            # 공원·녹지 안
            shaded = why != "sun"
            if not shaded and skyline and sun is not None:
                cell = skyline.get(f"{round(mla, 4):.4f}:{round(mlo, 4):.4f}")
                if cell is not None and cell.is_sun_blocked(sun[0], sun[1]):
                    shaded = True
                    why = "bldg"         # 스카이라인 격자 — 건물이 태양을 막았다
                    g.skyline_shaded_edges += 1
            # 비용에는 **순수 재질**을 넘긴다. 자전거도로 우대는 비용에 안 넣는다(_bike_class 주석).
            cost, ts, mrt_h = edge_cost(surface, shaded, cond, underground=under)
            a, b = node(p["lat"], p["lon"]), node(q["lat"], q["lon"])
            g.adj[a].append((b, d, cost, ts, shaded, tagged is not None, mrt_h, why, surf_out))
            g.adj[b].append((a, d, cost, ts, shaded, tagged is not None, mrt_h, why, surf_out))
            g.edge_count += 1
    g.cond = cond
    return g


def nearest(g: Graph, lat: float, lon: float) -> tuple[int, float] | None:
    best, best_d = -1, float("inf")
    for i, (la, lo) in enumerate(g.coords):
        d = _haversine(lat, lon, la, lo)
        if d < best_d:
            best_d, best = d, i
    return (best, best_d) if best >= 0 else None


# ── 코스 탐색 ────────────────────────────────────────────────
WALK_SPEED_KMH = 4.0        # 설계값 — 냄새 맡으며 걷는 반려견 산책 속도
BIKE_SPEED_KMH = 15.0       # 평지 일상 자전거(ママチャリ 포함). 경주용이 아니다.
BIKE_MET = 4.5              # 평지 15 km/h 의 대사량. 보행은 약 2.0.
_RETRACE_PENALTY = 3.0      # 설계값 — 왔던 길로 돌아오는 데 매기는 벌점 배수


def _edge_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _dijkstra(g: Graph, src: int, dst: int,
              banned: dict[tuple[int, int], float],
              weight: str = "comfort") -> list[int] | None:
    """최소 경로. weight="comfort" 면 거리×구간비용(더위), "distance" 면 거리만 — 최단경로 비교용."""
    n = len(g.coords)
    dist = [float("inf")] * n
    prev = [-1] * n
    dist[src] = 0.0
    pq: list[tuple[float, int]] = [(0.0, src)]
    seen = [False] * n

    while pq:
        d, u = heapq.heappop(pq)
        if seen[u]:
            continue
        seen[u] = True
        if u == dst:
            break
        for (v, meters, cost, _ts, _sh, _kn, _mrt, _w, _sf) in g.adj[u]:
            if seen[v]:
                continue
            w = (meters if weight == "distance" else meters * max(cost, 0.1)) \
                * banned.get(_edge_key(u, v), 1.0)
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if dist[dst] == float("inf"):
        return None
    path, cur = [], dst
    while cur != -1:
        path.append(cur)
        cur = prev[cur]
    return path[::-1]


def _turn_node(g: Graph, src: int, bearing_deg: float, meters: float) -> int | None:
    """출발점에서 주어진 방위로 `meters` 쯤 떨어진 도로 노드."""
    la0, lo0 = g.coords[src]
    r = 6_371_000.0
    lat1, lon1 = math.radians(la0), math.radians(lo0)
    br, dr = math.radians(bearing_deg), meters / r
    lat2 = math.asin(math.sin(lat1) * math.cos(dr) + math.cos(lat1) * math.sin(dr) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(dr) * math.cos(lat1),
                             math.cos(dr) - math.sin(lat1) * math.sin(lat2))
    t_la, t_lo = math.degrees(lat2), math.degrees(lon2)

    best, best_d = -1, float("inf")
    for i, (la, lo) in enumerate(g.coords):
        if i == src:
            continue
        d = _haversine(t_la, t_lo, la, lo)
        if d < best_d:
            best_d, best = d, i
    # 목표 지점에서 너무 멀면(길이 없는 방향) 버린다
    return best if best >= 0 and best_d < meters * 0.6 else None


# 구간 PET — 경로 전체 공통인 기온·습도·바람에 구간별 MRT 만 갈아끼운다.
# 실패해도 경로는 그려져야 하므로 예외는 삼킨다.
def _seg_pet(c: "Conditions", mrt_h: float) -> float | None:
    try:
        from vpti_core.comfort import compute_pet
        r = compute_pet(tdb=c.air_c, tr=mrt_h, v=max(c.eff_wind_ms(), 0.3), rh=c.rh,
                        met=c.met_value())
        return float(r.value)
    except Exception:  # noqa: BLE001
        return None


def _summarize(g: Graph, nodes: list[int], bearing: float) -> dict[str, Any]:
    total_m = 0.0
    weighted_cost = 0.0
    max_ts = -999.0
    shaded_m = 0.0
    known_m = 0.0
    lane_m = 0.0                 # 자전거 전용·지정 구간 (표시용)
    side_m = 0.0                 # 보도 구간 (표시용)
    # 경로 전체의 **체감기후**(2026-09-23). 구간마다 PET 를 이미 내고 있었는데 경로 수준에서는
    # 그늘 비율만 내보내고 있었다. 길을 고를 때 사람이 실제로 느끼는 것은 그늘 비율이 아니라
    # 체감온도다 — 그늘이 적어도 바람이 통하고 노면이 찬 길이 더 시원할 수 있다.
    # 거리로 가중한 평균과 최대값을 함께 준다(가장 더운 구간이 곧 견디기의 한계다).
    pet_m = 0.0                  # PET 를 계산할 수 있었던 거리
    pet_wsum = 0.0
    max_pet = None
    # 구간별 값도 함께 내보낸다 (2026-09-12). 여기서 이미 전부 계산돼 있는데 평균만 내고
    # 버리고 있었다 → 경로 위에서 "어느 골목이 더운지"를 보여주려면 이 값이 필요하다.
    # 추가 계산도, 추가 API 호출도 없다. 기존 클라이언트는 이 필드를 안 읽으면 그만이다.
    segments: list[dict[str, Any]] = []
    for i in range(1, len(nodes)):
        a, b = nodes[i - 1], nodes[i]
        e = next((x for x in g.adj[a] if x[0] == b), None)
        if e is None:
            continue
        _v, meters, cost, ts, shaded, known, mrt_h, why, surf = e
        bcls = None
        if isinstance(surf, str) and "|" in surf:
            surf, bcls = surf.split("|", 1)
            if bcls == "lane":
                lane_m += meters
            elif bcls == "sidewalk":
                side_m += meters
        total_m += meters
        weighted_cost += cost * meters
        max_ts = max(max_ts, ts)
        if shaded:
            shaded_m += meters
        if known:
            known_m += meters
        seg = {
            "i": i - 1,                       # coords[i-1] → coords[i] 구간
            "m": round(meters, 1),
            "ts": round(ts, 1),               # 노면온도 ℃
            "shaded": bool(shaded),
            "known": bool(known),             # 노면 재질이 태그로 확인된 구간인가
            "night": bool(g.cond is not None and g.cond.ghi <= 5.0),
            "why": why,                   # 왜 그늘인가(또는 왜 양지인가)
            "surface": surf,              # 노면 재질 (known=False 면 도로유형에서 추정)
        }
        if bcls is not None:
            seg["bike"] = bcls            # lane | sidewalk | road (표시용, 비용 무관)
        if g.cond is not None:
            pet = _seg_pet(g.cond, mrt_h)
            if pet is not None:
                seg["pet"] = round(pet, 1)    # 체감기후 ℃ — 간이 MRT 기반 개략치
                pet_m += meters
                pet_wsum += pet * meters
                max_pet = pet if max_pet is None else max(max_pet, pet)
        segments.append(seg)
    if total_m <= 0:
        return {}
    return {
        "coords": [{"lat": g.coords[i][0], "lon": g.coords[i][1]} for i in nodes],
        "segments": segments,
        "meters": round(total_m),
        "seconds": round(total_m / (
            ((g.cond.speed_kmh or BIKE_SPEED_KMH) if g.cond is not None and g.cond.mode == "bike"
             else WALK_SPEED_KMH) * 1000 / 3600)),
        "mean_cost": round(weighted_cost / total_m, 2),
        "max_surface_temp_c": round(max_ts, 1),
        "shade_ratio": round(shaded_m / total_m, 3),
        # 개략치다(간이 MRT). 절대값은 점 조회의 정식 VPTI 와 ±2~3 ℃ 어긋난다 —
        # 경로 **사이의 비교**에 쓸 값이다. pet_cover 는 계산이 된 구간의 거리 비율.
        "mean_pet_c": (round(pet_wsum / pet_m, 1) if pet_m > 0 else None),
        "max_pet_c": (round(max_pet, 1) if max_pet is not None else None),
        "pet_cover": (round(pet_m / total_m, 3) if total_m > 0 else 0.0),
        "surface_known_ratio": round(known_m / total_m, 3),
        "bearing_deg": bearing,
        # 자전거 모드에서만 의미가 있다. **경로 선택에는 안 쓰였고 표시용이다.**
        "bike_lane_ratio": round(lane_m / total_m, 3),
        "bike_sidewalk_ratio": round(side_m / total_m, 3),
    }


def find_route(g: Graph, start: int, goal: int, via: int | None = None) -> dict[str, Any]:
    """A→B 편도 — **그늘 우선 경로**와 **최단 경로**를 같이 돌려준다 (2026-09-12, 일본 통근용).

    산책 코스(find_courses)와 비용 함수는 완전히 같다. 다른 건 모양뿐 — 순환이 아니라 편도다.
    일본의 킬러 케이스가 "역까지 15분 걷기"라서 이 형태가 필요했다(日陰ルート).
    두 경로를 같이 주는 이유: "2분 더 걸으면 그늘이 3배" 같은 **선택의 근거**가 있어야 사람이 움직인다.

    `via` (2026-09-16) — 가게를 들렀다 가는 경로. 앱이 A→V, V→B 를 **따로 두 번** 부르고
    있었는데, 그러면 도로망 조회·스카이라인 격자·그래프 구성이 전부 두 번 돈다(가장 무거운 세 가지다).
    같은 그래프 위에서 두 구간을 잇기만 하면 되므로 여기서 이어 붙인다. 요약은 **합친 경로 하나로**
    한 번만 낸다 — 앱에서 평균을 다시 내면 가중치가 틀어지고 최고 노면온도가 어긋난다.
    """
    out: dict[str, Any] = {}
    for key, mode in (("comfort", "comfort"), ("shortest", "distance")):
        if via is None:
            path = _dijkstra(g, start, goal, {}, weight=mode)
        else:
            p1 = _dijkstra(g, start, via, {}, weight=mode)
            p2 = _dijkstra(g, via, goal, {}, weight=mode)
            path = (p1 + p2[1:]) if (p1 and p2 and len(p1) >= 2 and len(p2) >= 2) else None
        if not path or len(path) < 2:
            continue
        r = _summarize(g, path, 0.0)
        r.pop("bearing_deg", None)
        if via is not None and p1:
            # 앱이 "역까지 5분, 거기서 3분" 처럼 **구간을 나눠** 보여줄 수 있게 자리를 알려준다.
            # 합친 요약만 주면 그 화면을 만들 수 없다. coords[via_index] 가 경유지다.
            r["via_index"] = len(p1) - 1
        out[key] = r
    c, sh = out.get("comfort"), out.get("shortest")
    if c and sh:
        out["gain"] = {
            "extra_meters": c["meters"] - sh["meters"],
            "extra_seconds": c["seconds"] - sh["seconds"],
            "shade_ratio_gain": round(c["shade_ratio"] - sh["shade_ratio"], 3),
            "cost_drop": round(sh["mean_cost"] - c["mean_cost"], 2),
            "surface_temp_drop": round(sh["max_surface_temp_c"] - c["max_surface_temp_c"], 1),
            "same": c["coords"] == sh["coords"],
        }
    return out


_BEARING_NAME = {0: "북", 45: "북동", 90: "동", 135: "남동",
                 180: "남", 225: "남서", 270: "서", 315: "북서"}


def find_courses(g: Graph, start: int, target_meters: float,
                 max_courses: int = 3) -> list[dict[str, Any]]:
    """8방위 반환점 → 비용 최소 경로 → 돌아올 땐 왔던 길에 벌점."""
    if not (0 <= start < len(g.coords)) or g.edge_count <= 10:
        return []

    found: list[dict[str, Any]] = []
    half = target_meters / 2.0

    for bearing in range(0, 360, 45):
        turn = _turn_node(g, start, float(bearing), half)
        if turn is None:
            continue
        out = _dijkstra(g, start, turn, {})
        if not out:
            continue
        banned = {_edge_key(out[i - 1], out[i]): _RETRACE_PENALTY for i in range(1, len(out))}
        back = _dijkstra(g, turn, start, banned)
        if not back:
            continue
        c = _summarize(g, out + back[1:], float(bearing))
        if not c:
            continue
        if not (target_meters * 0.55 < c["meters"] < target_meters * 1.7):
            continue
        c["name"] = f"{_BEARING_NAME[bearing]}쪽 코스"
        found.append(c)

    # 순위: 열이 주, 목표 시간 근접이 보조. 목표에서 20% 벗어날 때마다 0.5°C 벌점(설계값).
    def rank(c: dict[str, Any]) -> float:
        return c["mean_cost"] + abs(c["meters"] - target_meters) / target_meters * 2.5

    picked: list[dict[str, Any]] = []
    for c in sorted(found, key=rank):
        dup = any(abs(p["bearing_deg"] - c["bearing_deg"]) < 60
                  and abs(p["meters"] - c["meters"]) < target_meters * 0.2 for p in picked)
        if not dup:
            picked.append(c)
        if len(picked) >= max_courses:
            break
    return picked
