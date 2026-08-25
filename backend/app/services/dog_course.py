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


def edge_cost(surface: str, shaded: bool, c: Conditions) -> tuple[float, float]:
    """한 구간의 비용. **WalkWindow 와 같은 식**이다."""
    ts = surface_temp_c(c.air_c, c.ghi, max(c.wind_ms, 0.3), surface, shaded, c.rain)
    ghi_mrt = c.ghi * 0.15 if shaded else c.ghi
    mrt_h = c.air_c + ghi_mrt / 900.0 * 12.0
    mrt_d = mrt_at_dog_height(mrt_h, ts, c.withers_cm)
    w = wbgt_outdoor(c.air_c, c.rh, max(c.wind_ms, 0.3), mrt_d)
    return w + c.vuln_offset_c + max(0.0, ts - DAMAGE_THRESHOLD_C), ts


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
    adj: list[list[tuple[int, float, float, float, bool, bool]]] = field(default_factory=list)
    # adj[i] = [(to, meters, cost, surface_temp, shaded, surface_known), ...]
    edge_count: int = 0


def build_graph(elements: Iterable[dict[str, Any]], cond: Conditions) -> Graph:
    """Overpass 형식 elements → 그래프.

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

    # ② 보행 가능한 way 를 간선으로
    for el in els:
        tags = el.get("tags") or {}
        hw = tags.get("highway")
        geom = el.get("geometry") or []
        if hw not in WALKABLE or len(geom) < 2:
            continue
        if tags.get("area") == "yes" or tags.get("access") == "private" or tags.get("foot") == "no":
            continue

        tagged = _SURFACE_TAG.get(str(tags.get("surface", "")).lower())
        surface = tagged or surface_guess(hw)
        covered = tags.get("covered") == "yes" or (tags.get("tunnel") not in (None, "no"))
        tree_lined = tags.get("tree_lined") == "yes"

        for i in range(1, len(geom)):
            p, q = geom[i - 1], geom[i]
            if "lat" not in p or "lat" not in q:
                continue
            d = _haversine(p["lat"], p["lon"], q["lat"], q["lon"])
            if d < 0.5:
                continue
            shaded = covered or tree_lined or in_green((p["lat"] + q["lat"]) / 2,
                                                       (p["lon"] + q["lon"]) / 2)
            cost, ts = edge_cost(surface, shaded, cond)
            a, b = node(p["lat"], p["lon"]), node(q["lat"], q["lon"])
            g.adj[a].append((b, d, cost, ts, shaded, tagged is not None))
            g.adj[b].append((a, d, cost, ts, shaded, tagged is not None))
            g.edge_count += 1
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
_RETRACE_PENALTY = 3.0      # 설계값 — 왔던 길로 돌아오는 데 매기는 벌점 배수


def _edge_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _dijkstra(g: Graph, src: int, dst: int,
              banned: dict[tuple[int, int], float]) -> list[int] | None:
    """비용 최소 경로. 가중치 = 거리 × 구간비용 × (되돌아가기 벌점)."""
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
        for (v, meters, cost, _ts, _sh, _kn) in g.adj[u]:
            if seen[v]:
                continue
            w = meters * max(cost, 0.1) * banned.get(_edge_key(u, v), 1.0)
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


def _summarize(g: Graph, nodes: list[int], bearing: float) -> dict[str, Any]:
    total_m = 0.0
    weighted_cost = 0.0
    max_ts = -999.0
    shaded_m = 0.0
    known_m = 0.0
    for i in range(1, len(nodes)):
        a, b = nodes[i - 1], nodes[i]
        e = next((x for x in g.adj[a] if x[0] == b), None)
        if e is None:
            continue
        _v, meters, cost, ts, shaded, known = e
        total_m += meters
        weighted_cost += cost * meters
        max_ts = max(max_ts, ts)
        if shaded:
            shaded_m += meters
        if known:
            known_m += meters
    if total_m <= 0:
        return {}
    return {
        "coords": [{"lat": g.coords[i][0], "lon": g.coords[i][1]} for i in nodes],
        "meters": round(total_m),
        "seconds": round(total_m / (WALK_SPEED_KMH * 1000 / 3600)),
        "mean_cost": round(weighted_cost / total_m, 2),
        "max_surface_temp_c": round(max_ts, 1),
        "shade_ratio": round(shaded_m / total_m, 3),
        "surface_known_ratio": round(known_m / total_m, 3),
        "bearing_deg": bearing,
    }


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
