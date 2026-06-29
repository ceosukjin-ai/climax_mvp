"""
도로축(road axis) 추출기 — 좌표 → 그 지점 도로의 방위각 [0,180°).

PWI 수학식 2는 풍향과 도로축의 각도차 Δθ 를 쓰는데, 도로축 추출기가 없어
그동안 0° 하드코딩(가정값)을 썼다. 본 모듈이 그 가정을 실제 도로 방위각으로
대체한다.

우선순위:
  ① OSM(OpenStreetMap) — Overpass API 로 좌표 인근 highway way 들을 받아,
     쿼리 지점에 가장 가까운 도로 세그먼트의 방위각을 산출 (source="osm").
  ② GPS 추적 — 보행 추적의 연속 GPS 점 이동방향으로 추정 (source="gps").
  ③ 가정값 — 위 둘 다 실패하면 기존처럼 가정값 사용 (source="assumed").

도로축은 양방향성(주기 180°)이므로 모든 방위각을 [0,180) 로 정규화한다.
'osm' 은 실측 DB(도로 geometry) 기반, 'gps' 는 추정, 'assumed' 는 가정이다.

osmnx 미설치 환경이라 Overpass API 를 httpx 로 직접 호출한다(추가 의존성 없음).
Overpass 는 User-Agent 가 없으면 406 을 반환하므로 반드시 헤더를 붙인다.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import httpx
from loguru import logger

# Overpass 공개 미러 — 앞에서부터 시도, HTTP 오류(504 등)면 다음 미러로.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
USER_AGENT = "ClimaX-MVP/1.0 (road-axis extractor)"
EARTH_RADIUS_M = 6_371_000.0

RoadAxisSource = Literal["osm", "gps", "assumed"]


class RoadAxisError(Exception):
    """도로축 추출 실패 (네트워크/파싱/도로 없음)."""


@dataclass(frozen=True, slots=True)
class RoadAxisResult:
    """도로축 추출 결과."""

    road_axis_deg: float          # 도로 방위각 [0,180)
    source: RoadAxisSource        # osm | gps | assumed
    # 메타데이터 (해석·디버깅용)
    osm_way_id: int | None = None
    osm_name: str | None = None
    osm_highway: str | None = None
    distance_m: float | None = None   # 최근접 세그먼트까지 거리 [m]
    note: str = ""

    @property
    def is_measured(self) -> bool:
        """OSM 도로 geometry 기반이면 실측(DB)로 간주."""
        return self.source == "osm"

    def as_dict(self) -> dict:
        return {
            "road_axis_deg": round(self.road_axis_deg, 1),
            "source": self.source,
            "osm_way_id": self.osm_way_id,
            "osm_name": self.osm_name,
            "osm_highway": self.osm_highway,
            "distance_m": round(self.distance_m, 1) if self.distance_m is not None else None,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# 기하 유틸
# ---------------------------------------------------------------------------
def normalize_axis(bearing_deg: float) -> float:
    """방위각 → 도로축 [0,180) (양방향성: 주기 180°)."""
    return bearing_deg % 180.0


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """A→B 초기 방위각 [0,360) (0=북, 90=동)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0


def _to_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """국소 등거리 투영 [m] (소거리용, 쿼리점 기준)."""
    x = math.radians(lon - lon0) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def _point_segment_distance_m(
    plat: float, plon: float,
    alat: float, alon: float,
    blat: float, blon: float,
) -> float:
    """점 P 에서 선분 AB 까지 거리 [m] (국소 평면 근사)."""
    px, py = _to_xy(plat, plon, plat, plon)  # (0,0)
    ax, ay = _to_xy(alat, alon, plat, plon)
    bx, by = _to_xy(blat, blon, plat, plon)
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
    t = min(1.0, max(0.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


# ---------------------------------------------------------------------------
# ① OSM (Overpass)
# ---------------------------------------------------------------------------
def _build_overpass_query(lat: float, lon: float, radius_m: int) -> str:
    return (
        f"[out:json][timeout:25];"
        f"way(around:{radius_m},{lat},{lon})[highway];"
        f"out geom;"
    )


def _nearest_segment_axis(
    lat: float, lon: float, ways: list[dict]
) -> tuple[float, float, dict, tuple[dict, dict]]:
    """모든 way 의 모든 세그먼트 중 쿼리점 최근접 세그먼트의 방위각 산출.

    returns: (axis_deg, distance_m, way, (node_a, node_b))
    """
    best_dist = math.inf
    best: tuple[float, dict, tuple[dict, dict]] | None = None
    for way in ways:
        geom = way.get("geometry") or []
        for a, b in zip(geom, geom[1:]):
            d = _point_segment_distance_m(lat, lon, a["lat"], a["lon"], b["lat"], b["lon"])
            if d < best_dist:
                best_dist = d
                brg = bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"])
                best = (normalize_axis(brg), way, (a, b))
    if best is None:
        raise RoadAxisError("OSM way 에 유효한 세그먼트(점 2개 이상)가 없음")
    axis, way, nodes = best
    return axis, best_dist, way, nodes


async def road_axis_from_osm(
    lat: float,
    lon: float,
    radius_m: int = 50,
    client: httpx.AsyncClient | None = None,
    timeout_sec: float = 40.0,
) -> RoadAxisResult:
    """Overpass 로 인근 도로 geometry 를 받아 최근접 세그먼트 방위각 산출.

    radius_m 에서 도로를 못 찾으면 2배·4배로 한 번씩 넓혀 재시도.
    Raises:
        RoadAxisError: 네트워크/HTTP/파싱 실패 또는 반경 내 도로 없음.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=timeout_sec, headers={"User-Agent": USER_AGENT})
    radii = (radius_m, radius_m * 2, radius_m * 4)
    last_http_err: Exception | None = None
    try:
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                for r in radii:
                    query = _build_overpass_query(lat, lon, r)
                    resp = await client.post(
                        endpoint, data={"data": query},
                        headers={"User-Agent": USER_AGENT},
                    )
                    resp.raise_for_status()
                    try:
                        elements = resp.json().get("elements", [])
                    except ValueError as e:
                        raise RoadAxisError(
                            f"Overpass 응답 JSON 파싱 실패: {resp.text[:150]}"
                        ) from e
                    ways = [el for el in elements if el.get("type") == "way"]
                    if ways:
                        axis, dist, way, _nodes = _nearest_segment_axis(lat, lon, ways)
                        tags = way.get("tags", {})
                        return RoadAxisResult(
                            road_axis_deg=axis,
                            source="osm",
                            osm_way_id=way.get("id"),
                            osm_name=tags.get("name"),
                            osm_highway=tags.get("highway"),
                            distance_m=dist,
                            note=f"Overpass around:{r}m, {len(ways)}개 way 중 최근접 세그먼트",
                        )
                    logger.debug("OSM 도로 없음 (radius={}m), 반경 확대", r)
                # 이 미러는 응답했으나 어느 반경에도 도로 없음 → 확정 (미러 바꿔도 동일 데이터)
                raise RoadAxisError(f"반경 {radii[-1]}m 내 도로(highway) 없음")
            except httpx.HTTPError as e:
                last_http_err = e
                logger.debug("Overpass 미러 실패 {} → 다음 미러: {}", endpoint, e)
                continue
        raise RoadAxisError(f"모든 Overpass 미러 호출 실패: {last_http_err}")
    finally:
        if own_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# ② GPS 추적 (fallback)
# ---------------------------------------------------------------------------
def road_axis_from_track(points: Sequence[tuple[float, float]]) -> RoadAxisResult:
    """연속 GPS 점들의 이동방향으로 도로축 추정.

    인접 점쌍 방위각을 단위벡터 평균(원형 평균)해 대표 진행방향을 구하고,
    양방향성(180° 주기)으로 정규화한다. 점 2개 미만이면 RoadAxisError.
    """
    pts = [p for p in points]
    if len(pts) < 2:
        raise RoadAxisError("GPS 점이 2개 미만 — 이동방향 추정 불가")

    # 도로축은 180° 주기 → 2θ 로 변환해 원형 평균(방향 반전 무관).
    sx = sy = 0.0
    n = 0
    for (la1, lo1), (la2, lo2) in zip(pts, pts[1:]):
        if (la1, lo1) == (la2, lo2):
            continue
        b = math.radians(bearing_deg(la1, lo1, la2, lo2) * 2.0)
        sx += math.cos(b)
        sy += math.sin(b)
        n += 1
    if n == 0:
        raise RoadAxisError("GPS 점들이 모두 동일 위치 — 이동방향 없음")
    mean2 = math.degrees(math.atan2(sy, sx)) % 360.0
    axis = normalize_axis(mean2 / 2.0)
    return RoadAxisResult(
        road_axis_deg=axis,
        source="gps",
        note=f"GPS {n}개 구간 이동방향 원형평균",
    )


# ---------------------------------------------------------------------------
# 디스패처
# ---------------------------------------------------------------------------
async def get_road_axis(
    lat: float,
    lon: float,
    *,
    track: Sequence[tuple[float, float]] | None = None,
    assumed_deg: float = 0.0,
    radius_m: int = 50,
    client: httpx.AsyncClient | None = None,
) -> RoadAxisResult:
    """① OSM → ② GPS 추적 → ③ 가정값 순으로 도로축 산출.

    어느 단계도 예외를 밖으로 던지지 않고, 마지막엔 항상 assumed 로 폴백한다
    (위 단계 실패 사유는 note 에 기록).
    """
    # ① OSM
    try:
        return await road_axis_from_osm(lat, lon, radius_m=radius_m, client=client)
    except RoadAxisError as e:
        osm_err = str(e)
        logger.warning("도로축 OSM 추출 실패 → fallback: {}", osm_err)

    # ② GPS 추적
    if track is not None:
        try:
            res = road_axis_from_track(track)
            return RoadAxisResult(
                road_axis_deg=res.road_axis_deg, source="gps",
                note=f"{res.note} (OSM 실패: {osm_err})",
            )
        except RoadAxisError as e:
            logger.warning("도로축 GPS 추정 실패 → 가정값: {}", e)

    # ③ 가정값
    return RoadAxisResult(
        road_axis_deg=normalize_axis(assumed_deg),
        source="assumed",
        note=f"OSM·GPS 모두 실패 → 가정값 {assumed_deg}° (OSM 실패: {osm_err})",
    )
