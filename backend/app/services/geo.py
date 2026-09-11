"""
건물 기하(GIS) 서비스 — 건물 방위 산출 (2026-08-14 신규).

## 왜 필요한가
실내 열환경에서 **가장 큰 미반영 변수가 건물 방위**다. 같은 아파트, 같은 층이라도
서향 세대는 여름 저녁에 외피가 달궈져 실내가 몇 도씩 높아지고, 북향은 거의 안 받는다.
지금까지 엔진은 이걸 완전히 같게 봤다.

## 왜 V-World가 아니라 OSM인가
- V-World **3D 데이터 API는 폐쇄**됐다.
- V-World 2D도 실서버(디엔에이클라우드)에서 **아웃바운드가 막혀 있다**
  (`실내체감_장애복구_NCP전환.md` 참조 — 미해결이라 리버스지오코딩도 NCP로 우회 중).
- OSM Overpass는 **키 불필요·무료·도달 가능**하고 국내 아파트 단지 커버리지가 쓸 만하다.
- V-World 연결이 복구되면 `_from_vworld()`를 추가해 우선 경로로 바꾸면 된다
  (인터페이스는 그대로 두었다).

## 정확도 한계 — 반드시 알고 쓸 것
- 건물 **장축**은 알 수 있지만 **몇 호인지는 모른다.** 판상형 아파트는 장축에 수직인
  두 방향(예: 남/북)에 세대가 갈리는데, 어느 쪽인지는 좌표로 알 수 없다.
  → 기본값은 **일사를 더 받는 쪽(보수적)**. 온보딩에서 "창문이 어느 쪽인가요?"를
    받으면 그 값이 우선한다.
- 타워형(정사각형에 가까운 평면)은 장축 자체가 의미가 없다 → `elongation`으로 걸러낸다.
- OSM에 건물이 없으면 None. 호출측은 기존 동작을 유지하면 된다.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass

import httpx
from loguru import logger

from app.config import get_settings

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
VWORLD_DATA_URL = "https://api.vworld.kr/req/data"

# ⚠️ V-World 2D 데이터 API의 **건물 레이어 코드**.
#    공식 문서 페이지가 크롤링 차단이라 코드값을 확정하지 못했다.
#    브이월드 개발자센터 > 2D 데이터API > 데이터 목록에서 '건물' 레이어의 ID를 확인해
#    이 값만 바꾸면 V-World가 우선 경로로 동작한다(키는 이미 .env.prod에 있음).
#    맞지 않으면 조용히 실패하고 OSM으로 넘어가므로 서비스에는 영향이 없다.
VWORLD_BUILDING_LAYER = "LT_C_SPBD"

# 건물은 변하지 않는다 — 성공값은 사실상 영구 캐시(30일), 실패는 짧게.
_CACHE: dict[tuple[float, float], tuple[float, "BuildingGeometry | None"]] = {}
_CACHE_TTL_SEC = 30 * 24 * 3600
_NEG_CACHE_TTL_SEC = 6 * 3600

# 이 비율 미만이면 '정사각형에 가까움' = 타워형 → 방위가 의미 없다고 본다.
MIN_ELONGATION = 1.25
# 2026-08-15: 40 → 100. 이웃 건물 차폐(SVF)까지 계산하려면 주변 100m는 봐야 한다.
# (25층 아파트 = 70m 높이 — 100m 거리에서도 저층에 그림자를 드리운다.)
SEARCH_RADIUS_M = 100
# 층고 근사 (기압 층 추정과 동일한 값 사용)
FLOOR_HEIGHT_M = 2.8
# 차폐 시 일사 배율 — 직달일사가 사라지고 산란 성분만 남는다.
SHADED_GAIN = 0.35
MAX_NEIGHBORS = 12

# === 사전적재 건물 저장소 (2026-09-08, 전세계 파일럿) ===
# 프로덕션 서버는 Overpass 아웃바운드가 막혀 있다(일본 등 해외 건물 실시간 조회 불가).
# 타깃 도시 건물을 0.01°(≈1.1km) 타일로 미리 적재해두고 로컬에서 조회한다.
# 형식은 Overpass 그대로({"elements":[{geometry,tags,id}]}) — _rings_from_osm과 동일 파싱.
_LOCAL_BUILDING_DIR = os.environ.get("LOCAL_BUILDING_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "buildings"
)
_LOCAL_TILE_CACHE: dict[str, list] = {}
_BAD_TILES: set[str] = set()          # 파싱 실패한 타일(잘린 파일) — 없는 것으로 취급
# ⚠️ 2026-09-11 사고: 타일 400개 캐시 × 배치 8프로세스로 16GB 전부 소진 → 서버 다운(디엔에이클라우드 재부팅).
# 타일 하나(수백 KB JSON)는 파이썬 객체로 5~10배 부풀어 5~7MB. 상한은 "프로세스 하나가 최악에 얼마를 쓰나"로 잡는다.
_LOCAL_TILE_CACHE_MAX = int(os.environ.get("LOCAL_TILE_CACHE_MAX", "32"))     # ≈ 200MB/프로세스
_RINGS_CACHE_MAX = int(os.environ.get("RINGS_CACHE_MAX", "4000"))


def _height_m_from_props(props: dict, default_floors: int | None = None) -> float | None:
    """건물 절대높이[m]. OSM height 태그(실측 m) 우선, 없으면 층수×층고.

    일본 OSM은 height 태그가 풍부(시부야 102동 중 71동) → 층수만인 한국보다 정확.
    높이를 전혀 모르고 default_floors도 없으면 None(그림자 지어내기 금지).
    """
    h = props.get("height")
    if h is not None:
        try:
            return float(str(h).strip().split()[0])
        except (TypeError, ValueError, IndexError):
            pass
    try:
        floors = int(props.get("gro_flo_co") or props.get("building:levels") or 0)
    except (TypeError, ValueError):
        floors = 0
    if floors <= 0:
        if default_floors is None:
            return None
        floors = default_floors
    return floors * FLOOR_HEIGHT_M


@dataclass
class Neighbor:
    """이웃 건물 하나 — 차폐(그림자) 계산용 (2026-08-15)."""
    az_deg: float             # 우리 위치에서 본 이웃 건물 중심의 방위각
    half_deg: float           # 그 건물이 가리는 각도 반폭 (방위각 기준)
    dist_m: float             # 외곽선까지 거리
    height_m: float           # 건물 높이 (층수 × 2.8m)
    label: str                # "제124동(23층)" 등 — 설명 문구용


@dataclass
class BuildingGeometry:
    long_axis_deg: float      # 건물 장축 방위각 (0=북, 90=동)
    facade_a_deg: float       # 주 외피 법선 ① (장축 +90°)
    facade_b_deg: float       # 주 외피 법선 ② (반대편)
    elongation: float         # 장축/단축 비 — 1에 가까우면 타워형
    is_slab: bool             # 판상형으로 볼 수 있는가
    source: str               # "V-World" / "OSM"
    neighbors: list = None    # list[Neighbor] — 층수를 아는 이웃 건물만


def _to_local_m(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """위경도 → 국지 평면 좌표(m). 건물 크기(수십 m)에서는 오차 무시 가능."""
    x = (lon - lon0) * 111_320.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 110_540.0
    return x, y


def _azimuth_of(vx: float, vy: float) -> float:
    """벡터의 방위각 (0=북, 90=동, 시계방향)."""
    return math.degrees(math.atan2(vx, vy)) % 360.0


def _principal_axis(pts: list[tuple[float, float]]) -> tuple[float, float]:
    """주성분 분석으로 장축 방위각과 장단축 비를 구한다.

    최소외접사각형보다 간단하고, 꼭짓점이 많은 실제 건물 외곽선에서 더 안정적이다.
    """
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - cx) ** 2 for p in pts) / n
    syy = sum((p[1] - cy) ** 2 for p in pts) / n
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts) / n

    # 2x2 공분산 행렬의 고유값·고유벡터 (해석해)
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = max(0.0, tr * tr / 4.0 - det)
    l1 = tr / 2.0 + math.sqrt(disc)          # 큰 고유값 = 장축
    l2 = max(1e-9, tr / 2.0 - math.sqrt(disc))

    if abs(sxy) > 1e-9:
        vx, vy = l1 - syy, sxy
    else:
        vx, vy = (1.0, 0.0) if sxx >= syy else (0.0, 1.0)

    return _azimuth_of(vx, vy) % 180.0, math.sqrt(l1 / l2)


def _point_in_ring(px: float, py: float, ring: list[tuple[float, float]]) -> bool:
    """다각형 내부 판정 (ray casting)."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xint = (x2 - x1) * (py - y1) / (y2 - y1 + 1e-12) + x1
            if px < xint:
                inside = not inside
    return inside


async def building_geometry(
    lat: float,
    lon: float,
    name_hint: str | None = None,      # 건축물대장에서 이미 아는 건물명 (예: "연산엘지아파트")
    floors_hint: int | None = None,    # 〃 층수 — 동(폴리곤)별 층수와 대조
) -> BuildingGeometry | None:
    """좌표가 속한 건물의 방위를 구한다. 없거나 실패하면 None.

    2026-08-15 개선 — 실데이터(연산엘지 단지) 검증에서 발견한 문제 반영:
      · **중심점 거리로 고르면 엉뚱한 건물이 잡힌다.** 아파트처럼 긴 건물은 중심이
        멀어서, 등록 좌표(단지 마당)에서 옆 상가(중심 19m)가 122동(중심 34m)을
        이겼다. → **외곽선까지의 최단거리**로 변경.
      · 건축물대장에서 이미 아는 **건물명·층수와 교차 대조** — 이름이 맞는 동에
        보너스를 줘서 단지 옆 부속 건물로 새지 않게 한다.
    """
    key = (round(lat, 4), round(lon, 4))
    hit = _CACHE.get(key)
    if hit is not None:
        ttl = _CACHE_TTL_SEC if hit[1] is not None else _NEG_CACHE_TTL_SEC
        if time.time() - hit[0] < ttl:
            return hit[1]

    result: BuildingGeometry | None = None
    rings, src = await _rings_cached(lat, lon)
    best = _pick_ring(rings, name_hint, floors_hint) if rings else None
    if best is not None:
        axis, elong = _principal_axis(best)
        result = BuildingGeometry(
            long_axis_deg=round(axis, 1),
            facade_a_deg=round((axis + 90.0) % 360.0, 1),
            facade_b_deg=round((axis + 270.0) % 360.0, 1),
            elongation=round(elong, 2),
            is_slab=elong >= MIN_ELONGATION,
            source=src,
            neighbors=_collect_neighbors(rings, best),
        )

    _CACHE[key] = (time.time(), result)
    return result


# 원시 폴리곤 캐시 — building_geometry(실내)와 sun_blocked_outdoor(실외)가 공유.
_RINGS_CACHE: dict[
    tuple[float, float],
    tuple[float, list[tuple[list[tuple[float, float]], dict]] | None, str],
] = {}


async def _rings_cached(
    lat: float, lon: float
) -> tuple[list[tuple[list[tuple[float, float]], dict]], str]:
    """건물 폴리곤 조회 + 캐시. V-World 우선, 실패 시 OSM. 실패는 짧게 캐시."""
    key = (round(lat, 4), round(lon, 4))
    _trim_caches()
    hit = _RINGS_CACHE.get(key)
    if hit is not None:
        ttl = _CACHE_TTL_SEC if hit[1] else _NEG_CACHE_TTL_SEC
        if time.time() - hit[0] < ttl:
            return hit[1] or [], hit[2]

    # 순서(2026-09-11): 로컬 타일 먼저 — 국내는 GIS건물통합+건축물대장 조인 타일(층수 완전), 해외는 OSM/PLATEAU 타일.
    # 타일이 없는 지역은 []를 돌려 V-World(국내 실시간) → OSM 순으로 폴백.
    sources = [("Local", _rings_from_local), ("V-World", _rings_from_vworld), ("OSM", _rings_from_osm)]
    if BUILDING_SOURCE == "db":
        sources.insert(0, ("DB", _rings_from_db))
    for name, fn in sources:
        try:
            rings = await fn(lat, lon)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"building rings[{name}] failed ({type(e).__name__}): {e}")
            continue
        if rings:
            _RINGS_CACHE[key] = (time.time(), rings, name)
            return rings, name
        if name == "DB" and await _db_tile_loaded(f"{int(math.floor(lat * 100))}_{int(math.floor(lon * 100))}"):
            # 적재된 타일인데 건물이 없다 = 정말 개활. 실시간 조회로 넘어가지 않는다.
            _RINGS_CACHE[key] = (time.time(), None, "DB")
            return [], "DB"
        if name == "Local" and _local_tile_exists(lat, lon):
            # 타일 파일이 있는데 건물이 없다 = 정말 개활(공원·바다·산). 실시간 호출로 넘어가지 않는다 (2026-09-11).
            _RINGS_CACHE[key] = (time.time(), None, "Local")
            return [], "Local"
    _RINGS_CACHE[key] = (time.time(), None, "")
    return [], ""


def _trim_caches() -> None:
    """메모리 상한 — 전국 격자 배치(60만+칸)·전국 타일에서 무한 성장 방지 (2026-09-11). 오래된 절반을 버린다."""
    if len(_RINGS_CACHE) > _RINGS_CACHE_MAX:
        for k in list(_RINGS_CACHE)[: len(_RINGS_CACHE) // 2]:
            _RINGS_CACHE.pop(k, None)
    if len(_LOCAL_TILE_CACHE) > _LOCAL_TILE_CACHE_MAX:
        for k in list(_LOCAL_TILE_CACHE)[: len(_LOCAL_TILE_CACHE) // 2]:
            _LOCAL_TILE_CACHE.pop(k, None)


async def sun_blocked_outdoor(
    lat: float,
    lon: float,
    sun_azimuth_deg: float,
    sun_elevation_deg: float,
    eye_height_m: float = 1.5,
) -> tuple[bool, str | None]:
    """실외 보행자 기준 — 지금 태양이 건물 뒤에 있는가 (2026-08-16).

    실외 MRT의 오랜 공백: SVF는 등방이라 "태양 방향의 건물"을 못 본다.
    건물 그늘에 서 있어도 직달일사가 통째로 들어가던 것을, 실내 이웃차폐(8/15)와
    같은 폴리곤·같은 기하로 판정한다. True면 직달(DNI) 차단 — 산란·장파는 그대로.

    실내와 다른 점: **모든 건물이 차폐 후보**다 (실내는 자기 건물을 방위 계산에
    쓰므로 이웃에서 제외하지만, 보행자에겐 바로 옆 건물이 가장 큰 그늘이다).
    층수를 모르는 건물은 넣지 않는다 — 그림자 지어내기 금지.
    """
    if sun_elevation_deg <= 0.0:
        return False, None
    # 스카이라인 격자 히트면 즉시 (2026-09-10): horizon[태양방위] > 태양고도 → 그늘.
    # 층수 결측 건물도 2층으로 포함되므로(폴리곤 경로는 결측 건물을 버림) 저층 주택가 그늘 재현율이 오른다.
    try:
        from app.services import skyline as _sky
        _cell = await _sky.get_cell(lat, lon)
    except Exception:  # noqa: BLE001
        _cell = None
    if _cell is not None:
        _b = _cell.is_sun_blocked(sun_azimuth_deg, sun_elevation_deg)
        return _b, ("스카이라인 그늘" if _b else None)
    rings, _src = await _rings_cached(lat, lon)
    if not rings:
        return False, None
    # GPS가 건물 외곽선 안으로 튄 경우 그 건물은 판정 불가 — 제외.
    outside = [(r, p) for r, p in rings if not _point_in_ring(0.0, 0.0, r)]
    for n in _collect_neighbors(outside, home_ring=None):
        d_az = abs(((sun_azimuth_deg - n.az_deg + 180) % 360) - 180)
        if d_az > n.half_deg + 2.0:
            continue
        rise = n.height_m - eye_height_m
        if rise <= 0:
            continue
        if sun_elevation_deg < math.degrees(math.atan2(rise, n.dist_m)):
            return True, f"{n.label} 그늘"
    return False, None


def _ray_ring_hit(dx: float, dy: float, ring: list[tuple[float, float]]) -> float | None:
    """원점(0,0)에서 방위벡터 (dx,dy) 로 쏜 광선이 폴리곤 외곽선에 처음 닿는 거리(m). 없으면 None."""
    best = None
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        ex, ey = x2 - x1, y2 - y1
        det = ex * dy - dx * ey
        if abs(det) < 1e-9:
            continue
        t = (-x1 * ey + ex * y1) / det          # 광선 파라미터(거리)
        u = (dx * y1 - dy * x1) / det            # 세그먼트 파라미터 [0,1]
        if t > 0.0 and 0.0 <= u <= 1.0:
            if best is None or t < best:
                best = t
    return best


def _closest_on_ring(px: float, py: float, ring: list[tuple[float, float]]) -> tuple[float, float]:
    """점에서 폴리곤 외곽선까지 최근접점."""
    best = None
    bd = float("inf")
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]; x2, y2 = ring[i + 1]
        ex, ey = x2 - x1, y2 - y1
        L2 = ex * ex + ey * ey
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * ex + (py - y1) * ey) / L2))
        qx, qy = x1 + t * ex, y1 + t * ey
        d = (qx - px) ** 2 + (qy - py) ** 2
        if d < bd:
            bd, best = d, (qx, qy)
    return best or (px, py)


def _snap_outside(rings: list, clearance_m: float = 1.0, max_iter: int = 3) -> tuple[list, float]:
    """GPS 오차로 점이 건물 폴리곤 안에 떨어지면, 가장 가까운 외곽선 밖 clearance_m 로 이동
    (2026-09-09). 좁은 골목(3~5m)에서 5~10m GPS 오차면 흔함 — 그 건물을 '제외'하면 폭·SVF가
    블록 반대편까지 열려 크게 틀린다. 반환: (평행이동된 rings, 이동거리 m). 원점은 (0,0) 유지."""
    ox = oy = 0.0
    moved = 0.0
    for _ in range(max_iter):
        inside = next((r for r, _p in rings if len(r) >= 4 and _point_in_ring(ox, oy, r)), None)
        if inside is None:
            break
        qx, qy = _closest_on_ring(ox, oy, inside)
        dx, dy = qx - ox, qy - oy
        n = math.hypot(dx, dy)
        if n < 1e-6:              # 정확히 외곽선 위 → 폴리곤 중심 반대 방향으로
            cx = sum(x for x, _ in inside[:-1]) / (len(inside) - 1)
            cy = sum(y for _, y in inside[:-1]) / (len(inside) - 1)
            dx, dy = ox - cx, oy - cy
            n = math.hypot(dx, dy) or 1.0
        ox, oy = qx + dx / n * clearance_m, qy + dy / n * clearance_m
        moved = math.hypot(ox, oy)
    if moved == 0.0:
        return rings, 0.0
    shifted = [([(x - ox, y - oy) for x, y in r], p) for r, p in rings]
    return shifted, moved


def _shift_rings(rings: list, ox: float, oy: float) -> list:
    return [([(x - ox, y - oy) for x, y in r], p) for r, p in rings]


def _snap_to_street_center(rings: list, az_step_deg: int = 10, max_m: float = 30.0) -> tuple[list, float, float | None]:
    """벽 1m 밖 → **가로 중심선**으로 (2026-09-10). 마주보는 광선쌍 중 d1+d2 최소 = 가로 단면,
    그 중점으로 원점을 옮긴다. 사람은 골목 한가운데를 걷지 벽에 붙어 걷지 않는다 — 실측 80점
    진단에서 벽 0~2m에 붙은 좌표가 SVF 과차폐(0.3↔0.8)의 주범이었고, 중심선 스냅+도로축 ±4m
    중앙값으로 MAE 0.152→0.121, 편향 −0.057→0.00, r 0.31→0.40. 반환: (rings, 이동m, 도로축 방위|None)."""
    n = max(1, int(360 / az_step_deg))
    d: list[float | None] = [None] * n
    for i in range(n):
        az = math.radians(i * az_step_deg)
        dx, dy = math.sin(az), math.cos(az)
        for ring, _p in rings:
            if len(ring) < 4:
                continue
            t = _ray_ring_hit(dx, dy, ring)
            if t is not None and t <= max_m and (d[i] is None or t < d[i]):
                d[i] = t
    best = None
    for i in range(n // 2):
        j = i + n // 2
        if d[i] is None or d[j] is None:
            continue
        if best is None or d[i] + d[j] < best[0]:
            best = (d[i] + d[j], i, d[i], d[j])
    if best is None:
        return rings, 0.0, None
    _w, i, d1, d2 = best
    az = math.radians(i * az_step_deg)
    off = (d1 - d2) / 2.0
    return _shift_rings(rings, math.sin(az) * off, math.cos(az) * off), abs(off), (i * az_step_deg + 90) % 180


async def svf_geometric(
    lat: float, lon: float, eye_height_m: float = 1.5, az_step_deg: int = 2,
    default_floors: int = 2,
) -> dict:
    """건물 GIS 기하만으로 SVF — 방위별 ray-cast(건물 외곽선 실측 거리) 방식(2026-09-08 v2).

    V-World/OSM 건물 폴리곤+층수 → 각 방위(0~360)에서 건물 외곽선에 광선을 쏴 실제
    교차거리로 지평선 상승각 β(az) 산출 → SVF = 1 − mean(sin²β) (Oke/UMEP 표준).
    중심±각폭 근사(과차폐)를 버리고 모서리까지 정확 거리를 씀. 층수 결측은 보수적 기본높이.
    """
    # 스카이라인 격자 히트면 즉시 (2026-09-10, 사전계산). 미스면 아래 실시간 계산.
    try:
        from app.services import skyline as _sky
        _cell = await _sky.get_cell(lat, lon)
    except Exception:  # noqa: BLE001
        _cell = None
    if _cell is not None:
        return {"svf": _cell.svf, "source": f"skyline:{_cell.src}", "n_buildings": _cell.n_bld,
                "snapped_m": 0.0, "centered_m": _cell.centered_m, "street_axis_deg": _cell.axis_deg}
    rings, src = await _rings_cached(lat, lon)
    if not rings:
        return {"svf": None, "source": src or "", "n_buildings": 0, "reason": "건물 폴리곤 없음"}
    rings, snapped = _snap_outside(rings)   # GPS 오차로 건물 안이면 골목으로 끌어냄
    rings, centered, axis = _snap_to_street_center(rings)   # 벽 → 가로 중심선
    if axis is not None:
        # 도로축 따라 ±4m 3점의 중앙값 — GPS 5~10m 오차에 강건 (2026-09-10)
        vals = []
        ax = math.radians(axis)
        for off in (-4.0, 0.0, 4.0):
            rr, _ = _snap_outside(_shift_rings(rings, math.sin(ax) * off, math.cos(ax) * off))
            vals.append(_svf_from_rings(rr, eye_height_m, az_step_deg, default_floors))
        vals.sort()
        svf, nb = vals[1]
        return {"svf": svf, "source": src, "n_buildings": nb, "snapped_m": round(snapped, 1),
                "centered_m": round(centered, 1), "street_axis_deg": axis}
    svf, nb = _svf_from_rings(rings, eye_height_m, az_step_deg, default_floors)
    return {"svf": svf, "source": src, "n_buildings": nb, "snapped_m": round(snapped, 1), "centered_m": 0.0}


def _svf_from_rings(rings: list, eye_height_m: float, az_step_deg: int, default_floors: int) -> tuple[float, int]:
    """원점(0,0)에서 ray-cast SVF. 반환 (svf, 차폐 건물 수)."""
    # (외곽선 좌표, 높이) — 점을 품은 건물은 제외(그 안이면 판정불가), 층수결측은 기본높이
    blds: list[tuple[list[tuple[float, float]], float]] = []
    for ring, props in rings:
        if len(ring) < 4 or _point_in_ring(0.0, 0.0, ring):
            continue
        H = _height_m_from_props(props, default_floors=default_floors)
        if H is None:
            continue
        h = H - eye_height_m
        if h <= 0:
            continue
        blds.append((ring, h))
    if not blds:
        return 1.0, 0

    n_sectors = max(1, int(360 / az_step_deg))
    sin2_sum = 0.0
    for i in range(n_sectors):
        az = math.radians(i * az_step_deg)
        dx, dy = math.sin(az), math.cos(az)      # x=동, y=북, 방위 0=북
        beta_max = 0.0
        for ring, h in blds:
            t = _ray_ring_hit(dx, dy, ring)
            if t is None:
                continue
            beta = math.atan2(h, t)
            if beta > beta_max:
                beta_max = beta
        sin2_sum += math.sin(beta_max) ** 2
    svf = 1.0 - sin2_sum / n_sectors
    return round(max(0.0, min(1.0, svf)), 3), len(blds)


async def street_width_geometric(
    lat: float, lon: float, az_step_deg: int = 5, max_m: float = 60.0,
    default_floors: int = 2,
) -> dict:
    """건물 GIS 기하로 가로 폭 W·협곡비 H/W (2026-09-09).

    각 방위로 광선을 쏴 첫 건물 외곽선까지 거리 d(az). 마주보는 쌍 d(az)+d(az+180) 중
    최소가 가로 폭 W(도로축에 수직 방향), 그 방위의 양쪽 건물 평균높이 H → H/W.
    max_m 안에 양쪽 다 건물이 없으면 개방(폭 None). 위성 폭 분류 AI 의 라벨로도 쓴다.
    """
    try:
        from app.services import skyline as _sky
        _cell = await _sky.get_cell(lat, lon)
    except Exception:  # noqa: BLE001
        _cell = None
    if _cell is not None:
        return {"width_m": _cell.width_m, "hw_ratio": _cell.hw_ratio, "axis_deg": _cell.axis_deg,
                "source": f"skyline:{_cell.src}", "snapped_m": 0.0}
    rings, src = await _rings_cached(lat, lon)
    if not rings:
        return {"width_m": None, "hw_ratio": None, "axis_deg": None, "source": src or ""}
    rings, snapped = _snap_outside(rings)
    blds: list[tuple[list[tuple[float, float]], float]] = []
    for ring, props in rings:
        if len(ring) < 4 or _point_in_ring(0.0, 0.0, ring):
            continue
        H = _height_m_from_props(props, default_floors=default_floors) or 0.0
        blds.append((ring, H))
    if not blds:
        return {"width_m": None, "hw_ratio": None, "axis_deg": None, "source": src}
    n = max(1, int(360 / az_step_deg))
    d = [None] * n
    h = [0.0] * n
    for i in range(n):
        az = math.radians(i * az_step_deg)
        dx, dy = math.sin(az), math.cos(az)
        for ring, H in blds:
            t = _ray_ring_hit(dx, dy, ring)
            if t is not None and t <= max_m and (d[i] is None or t < d[i]):
                d[i], h[i] = t, H
    best = None
    half = n // 2
    for i in range(half):
        j = i + half
        if d[i] is None or d[j] is None:
            continue
        w = d[i] + d[j]
        if best is None or w < best[0]:
            best = (w, (h[i] + h[j]) / 2.0, i * az_step_deg)
    if best is None:
        return {"width_m": None, "hw_ratio": None, "axis_deg": None, "source": src,
                "reason": "양측 건물 없음(개방)"}
    w, hm, az_perp = best
    return {"width_m": round(w, 1), "hw_ratio": round(hm / w, 2) if w > 0 else None,
            "axis_deg": (az_perp + 90) % 180,     # 도로축 방향(폭 방향에 수직)
            "source": src, "snapped_m": round(snapped, 1)}


async def dominant_wall_material(
    lat: float, lon: float, eye_height_m: float = 1.5, default_floors: int = 2,
) -> dict:
    """관측점 주변 건물 용도/구조로 대표 외벽 재질 산출(2026-09-09, 벽재질 스테이지).

    각 주변 건물의 벽 복사 기여를 (높이/외곽선최단거리)로 가중해 albedo/emissivity 를
    가중평균한다(가깝고 높은 벽일수록 큰 기여). PLATEAU 構造種別(structure_code)이 있으면
    정밀 오버라이드, 없으면 OSM building= 용도 휴리스틱, 그것도 없으면 콘크리트 기본.
    반환: {material, albedo, emissivity, mix, n, source}.
    """
    from app.services import wall_material as _wm
    rings, src = await _rings_cached(lat, lon)
    weighted: list[tuple[str, float]] = []
    for ring, props in rings:
        if len(ring) < 4 or _point_in_ring(0.0, 0.0, ring):
            continue
        H = _height_m_from_props(props, default_floors=default_floors)
        if H is None or (H - eye_height_m) <= 0:
            continue
        dist = _dist_to_ring(0.0, 0.0, ring)
        if dist > SEARCH_RADIUS_M:
            continue
        w = (H - eye_height_m) / max(dist, 3.0)
        mat = None
        sc = props.get("structure_code") or props.get("plateau_struct")
        if sc is not None:
            mat = _wm.material_from_plateau(sc)
        if mat is None:
            mat = _wm.material_from_osm(props)
        weighted.append((mat, w))
    res = _wm.blend(weighted)
    res["n"] = len(weighted)
    res["source"] = src
    return res



def _dist_to_ring(px: float, py: float, ring: list[tuple[float, float]]) -> float:
    """점에서 다각형 **외곽선**까지의 최단거리(m). 중심점 거리가 아니다."""
    best = float("inf")
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 < 1e-12 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg2))
        best = min(best, math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)))
    return best


def _pick_ring(
    rings: list[tuple[list[tuple[float, float]], dict]],
    name_hint: str | None = None,
    floors_hint: int | None = None,
) -> list[tuple[float, float]] | None:
    """좌표를 품는 건물 > 대장 건물명 일치 > 외곽선 최단거리 순으로 고른다."""
    best, best_score = None, float("inf")
    for ring, props in rings:
        if len(ring) < 4:
            continue
        if _point_in_ring(0.0, 0.0, ring):
            return ring
        score = _dist_to_ring(0.0, 0.0, ring)
        pname = str(props.get("buld_nm") or props.get("name") or "")
        if name_hint and pname and (name_hint in pname or pname in name_hint):
            score -= 30.0          # 대장과 같은 건물명 — 사실상 확정 수준의 보너스
        try:
            pfloors = int(props.get("gro_flo_co") or props.get("building:levels") or 0)
            if floors_hint and pfloors == floors_hint:
                score -= 10.0      # 층수까지 일치 — 단지 내 여러 동 중 해당 동
        except (TypeError, ValueError):
            pass
        if score < best_score:
            best, best_score = ring, score
    return best


async def nearest_building_parcel_hint(
    lat: float, lon: float
) -> tuple[float, float, dict] | None:
    """가장 가까운 건물 폴리곤의 **중심 좌표(위경도)** 와 속성을 돌려준다 (2026-08-18).

    왜 필요한가: 리버스지오코딩은 '좌표에 가장 가까운 지번'을 준다. 마당·통로·주차장에
    GPS가 찍히면 건물이 없는 지번이 나오고 건축물대장 조회가 통째로 빈다
    (부산대 연구실 → "장전동 40", 사방 30m까지 훑어도 실패. 8/18 실측).
    건물 폴리곤의 **중심**에서 다시 지오코딩하면 '그 건물의 지번'이 나온다.

    같은 V-World 레이어(LT_C_SPBD)를 실내 방위 계산에서 이미 쓰고 있어 추가 비용이 거의 없다.
    """
    rings, _src = await _rings_cached(lat, lon)
    best = _pick_ring(rings) if rings else None
    if best is None or len(best) < 4:
        return None
    props: dict = {}
    for ring, pr in rings:
        if ring is best:
            props = pr
            break
    cx = sum(p[0] for p in best) / len(best)
    cy = sum(p[1] for p in best) / len(best)
    clat = lat + cy / 110_540.0
    clon = lon + cx / (111_320.0 * math.cos(math.radians(lat)))
    return clat, clon, props


def _load_local_tile(tkey: str) -> list:
    """0.01° 타일 하나의 건물 elements(Overpass 형식). 정적이라 메모리 캐시."""
    cached = _LOCAL_TILE_CACHE.get(tkey)
    if cached is not None:
        return cached
    path = os.path.join(_LOCAL_BUILDING_DIR, tkey + ".json")
    elements: list = []
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                elements = (json.load(f) or {}).get("elements") or []
        except Exception as e:  # noqa: BLE001
            # 배치가 중간에 죽으면 타일이 잘린 채 남는다. 이걸 "건물 없음"으로 쓰면 그 지역이 통째로
            # 개활(SVF 1.0)이 된다 → 깨진 타일은 '없는 것'으로 표시해 실시간 조회로 폴백 (2026-09-11).
            logger.warning(f"local building tile {tkey} load failed: {e}")
            _BAD_TILES.add(tkey)
            elements = []
    _LOCAL_TILE_CACHE[tkey] = elements
    return elements


async def _rings_from_local(
    lat: float, lon: float
) -> list[tuple[list[tuple[float, float]], dict]]:
    """사전적재 타일(3×3)에서 반경 내 건물 링. 서버 Overpass 차단 우회(해외 커버)."""
    base_la = int(math.floor(lat * 100))
    base_lo = int(math.floor(lon * 100))
    out: list[tuple[list[tuple[float, float]], dict]] = []
    seen: set = set()
    lim2 = (SEARCH_RADIUS_M + 60.0) ** 2
    for dla in (-1, 0, 1):
        for dlo in (-1, 0, 1):
            for el in _load_local_tile(f"{base_la + dla}_{base_lo + dlo}"):
                eid = el.get("id")
                if eid is not None:
                    if eid in seen:
                        continue
                    seen.add(eid)
                geom = el.get("geometry") or []
                if len(geom) < 4:
                    continue
                x0, y0 = _to_local_m(geom[0]["lat"], geom[0]["lon"], lat, lon)
                if x0 * x0 + y0 * y0 > lim2:       # 반경 밖 넉넉히 컷
                    continue
                ring = [_to_local_m(g["lat"], g["lon"], lat, lon) for g in geom]
                out.append((ring, dict(el.get("tags") or {})))
    await _fill_floors_from_register_many([p for _, p in out])
    return out


_LOCAL_TILE_EXISTS: dict[str, bool] = {}


def _local_tile_exists(lat: float, lon: float) -> bool:
    """타일 **파일**이 있는가 (없는 타일도 _LOCAL_TILE_CACHE 에 []로 들어가므로 캐시 키 존재로 판단하면 안 된다 — 2026-09-11 사고)."""
    tkey = f"{int(math.floor(lat * 100))}_{int(math.floor(lon * 100))}"
    if tkey in _BAD_TILES:
        return False
    v = _LOCAL_TILE_EXISTS.get(tkey)
    if v is None:
        v = os.path.isfile(os.path.join(_LOCAL_BUILDING_DIR, tkey + ".json"))
        if len(_LOCAL_TILE_EXISTS) > 50000:
            _LOCAL_TILE_EXISTS.clear()
        _LOCAL_TILE_EXISTS[tkey] = v
    return v


# === 건물 원천: PostGIS (2026-09-11) ===
# 왜: 타일 JSON 파일은 (1) 전국 약 40GB 로 WAS 디스크(50GB)에 안 들어가고 (2) 프로세스마다 메모리에
# 올라가 2026-09-11 서버 다운의 원인이 됐다. 같은 건물을 PostGIS 기하로 넣으면 5~8GB 에 인덱스 조회다.
# **계산은 한 줄도 바뀌지 않는다** — 같은 폴리곤·같은 태그를 같은 형식(rings)으로 돌려준다.
# 전환은 실측 80점(MAE 0.121 / bias -0.004 / r 0.40)이 그대로일 때만. 파일 경로는 폴백으로 남겨둔다.
# `BUILDING_SOURCE=db` 일 때만 우선 사용(기본 file).
BUILDING_SOURCE = os.environ.get("BUILDING_SOURCE", "file").lower()
_DB_TILE_CACHE: dict[str, list] = {}
_DB_TILE_LOADED: dict[str, bool] = {}


async def _db_tile_loaded(tkey: str) -> bool:
    """그 타일이 DB 에 적재됐는가 (파일 존재 확인과 같은 역할 — 적재된 곳은 DB 가 권위 원천)."""
    v = _DB_TILE_LOADED.get(tkey)
    if v is not None:
        return v
    try:
        from app.services.skyline import _get_pool
        pool = await _get_pool()
        if pool is None:
            return False
        async with pool.acquire() as c:
            v = bool(await c.fetchval("SELECT 1 FROM bldg_tile WHERE tkey=$1", tkey))
    except Exception:  # noqa: BLE001
        return False
    if len(_DB_TILE_LOADED) > 50000:
        _DB_TILE_LOADED.clear()
    _DB_TILE_LOADED[tkey] = v
    return v


async def _load_db_tile(tkey: str) -> list:
    """타일 하나의 건물(Overpass elements 형식). 타일 단위로 캐시 — 격자 배치가 칸마다 DB를 때리지 않게."""
    cached = _DB_TILE_CACHE.get(tkey)
    if cached is not None:
        return cached
    la_s, lo_s = tkey.split("_")
    s, w = int(la_s) / 100.0, int(lo_s) / 100.0
    elements: list = []
    try:
        from app.services.skyline import _get_pool
        pool = await _get_pool()
        if pool is None:
            return []
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, tags::text AS tags, ST_AsGeoJSON(geom) AS g FROM bldg_poly "
                "WHERE geom && ST_MakeEnvelope($1,$2,$3,$4,4326)", w, s, w + 0.01, s + 0.01)
        for r in rows:
            g = json.loads(r["g"]); t = g.get("type"); c_ = g.get("coordinates") or []
            rings = [c_[0]] if t == "Polygon" else [pp[0] for pp in c_ if pp]
            for k, ring in enumerate(rings):
                if len(ring) < 4:
                    continue
                elements.append({"id": r["id"] if k == 0 else f"{r['id']}:{k}",
                                 "geometry": [{"lat": y, "lon": x} for x, y in ring],
                                 "tags": json.loads(r["tags"])})
    except Exception as e:  # noqa: BLE001
        logger.warning("[bldg_poly] 타일 {} 조회 실패: {}", tkey, e)
        return []
    if len(_DB_TILE_CACHE) > _LOCAL_TILE_CACHE_MAX:
        for k in list(_DB_TILE_CACHE)[: len(_DB_TILE_CACHE) // 2]:
            _DB_TILE_CACHE.pop(k, None)
    _DB_TILE_CACHE[tkey] = elements
    return elements


async def _rings_from_db(
    lat: float, lon: float
) -> list[tuple[list[tuple[float, float]], dict]]:
    """PostGIS `bldg_poly` 에서 3×3 타일. 반환 형식은 _rings_from_local 과 완전히 동일."""
    base_la = int(math.floor(lat * 100))
    base_lo = int(math.floor(lon * 100))
    out: list[tuple[list[tuple[float, float]], dict]] = []
    seen: set = set()
    lim2 = (SEARCH_RADIUS_M + 60.0) ** 2
    for dla in (-1, 0, 1):
        for dlo in (-1, 0, 1):
            for el in await _load_db_tile(f"{base_la + dla}_{base_lo + dlo}"):
                eid = el.get("id")
                if eid is not None:
                    if eid in seen:
                        continue
                    seen.add(eid)
                geom = el.get("geometry") or []
                if len(geom) < 4:
                    continue
                x0, y0 = _to_local_m(geom[0]["lat"], geom[0]["lon"], lat, lon)
                if x0 * x0 + y0 * y0 > lim2:
                    continue
                out.append(([_to_local_m(g["lat"], g["lon"], lat, lon) for g in geom], dict(el.get("tags") or {})))
    await _fill_floors_from_register_many([p for _, p in out])
    return out


async def _rings_from_osm(
    lat: float, lon: float
) -> list[tuple[list[tuple[float, float]], dict]]:
    q = (
        f"[out:json][timeout:15];"
        f'way(around:{SEARCH_RADIUS_M},{lat},{lon})["building"];'
        f"out geom;"
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(OVERPASS_URL, data={"data": q})
        r.raise_for_status()
        out = []
        for el in r.json().get("elements") or []:
            geom = el.get("geometry") or []
            if len(geom) >= 4:
                ring = [_to_local_m(g["lat"], g["lon"], lat, lon) for g in geom]
                out.append((ring, el.get("tags") or {}))
        return out


# === 건축물대장 표제부 층수 보강 (2026-09-11) ===
# V-World 폴리곤(도로명주소 건물층)은 실측 80점에서 검증된 원천(r .40)이지만 층수 결측이 많다(부암 241/245).
# GIS건물통합 폴리곤으로 통째 교체해 보니 폴리곤 자체가 달라 r .34~.38로 오히려 나빠짐(2026-09-11 진단).
# → 폴리곤은 V-World 그대로 두고, 층수·높이만 표제부(PNU=bd_mgt_sn 앞 19자리)로 채운다.
# 파일: {LOCAL_BUILDING_DIR}/_pyojebu_floors_kr.json  {pnu19: [[동명, 지상층수, 높이m], ...]}  (scripts/build_pyojebu_floors.py)
_REGISTER: dict | None = None


def _load_register() -> dict:
    global _REGISTER
    if _REGISTER is None:
        path = os.path.join(_LOCAL_BUILDING_DIR, "_pyojebu_floors_kr.json")
        try:
            with open(path, encoding="utf-8") as f:
                _REGISTER = json.load(f) or {}
            logger.info("[register] 표제부 층수표 {}필지", len(_REGISTER))
        except FileNotFoundError:
            _REGISTER = {}
        except Exception as e:  # noqa: BLE001
            logger.warning("[register] 층수표 로드 실패: {}", e)
            _REGISTER = {}
    return _REGISTER


def _needs_floors(props: dict) -> str | None:
    """층수 결측이고 bd_mgt_sn 이 있으면 PNU19, 아니면 None."""
    try:
        if int(props.get("gro_flo_co") or 0) > 0:
            return None
    except (TypeError, ValueError):
        pass
    sn = str(props.get("bd_mgt_sn") or "")
    return sn[:19] if len(sn) >= 19 else None


async def _fill_floors_from_register_many(props_list: list[dict]) -> None:
    """여러 건물의 층수 결측을 한 번에 채운다 — PostGIS `bldg_register` 한 쿼리(전국 736만 동). DB 없으면 JSON 표 폴백."""
    pending: dict[str, list[dict]] = {}
    for p in props_list:
        pnu = _needs_floors(p)
        if pnu:
            pending.setdefault(pnu, []).append(p)
    if not pending:
        return
    # DB(bldg_register, 전국 736만 동) 우선 — JSON 표는 프로세스당 수백 MB라 DB 가 없을 때만 쓴다 (2026-09-11).
    try:
        from app.services.skyline import _get_pool
        pool = await _get_pool()
        if pool is None:
            reg = _load_register()
            for pnu in list(pending):
                rows = reg.get(pnu)
                if rows:
                    for p in pending.pop(pnu):
                        _apply_register_rows(p, rows)
            return
        async with pool.acquire() as c:
            recs = await c.fetch("SELECT pnu, dong, floors, height FROM bldg_register WHERE pnu = ANY($1::text[])",
                                 list(pending))
    except Exception as e:  # noqa: BLE001
        logger.debug("[register] DB 조회 생략: {}", e)
        return
    by: dict[str, list] = {}
    for r in recs:
        by.setdefault(r["pnu"], []).append([r["dong"] or "", int(r["floors"] or 0), float(r["height"] or 0.0)])
    for pnu, plist in pending.items():
        rows = by.get(pnu)
        if rows:
            for p in plist:
                _apply_register_rows(p, rows)


def _fill_floors_from_register(props: dict) -> None:
    """(동기·JSON 표 전용) V-World 속성에 층수가 없으면 표제부에서 채운다. 동 표기 일치 > 최대층."""
    pnu = _needs_floors(props)
    if not pnu:
        return
    rows = _load_register().get(pnu)
    if not rows:
        return
    _apply_register_rows(props, rows)


def _apply_register_rows(props: dict, rows: list) -> None:
    dong = str(props.get("buld_nm_dc") or "").strip()
    pick = None
    if dong:
        m = [r for r in rows if r[0] and (r[0] == dong or dong in r[0] or r[0] in dong)]
        if m:
            pick = max(m, key=lambda r: r[1])
    if pick is None:
        pick = max(rows, key=lambda r: r[1])
    if pick[1] > 0:
        props["gro_flo_co"] = int(pick[1])
        props["floors_src"] = "register"
    if pick[2] and pick[2] > 0 and not props.get("height"):
        props["height"] = float(pick[2])


async def _rings_from_vworld(
    lat: float, lon: float
) -> list[tuple[list[tuple[float, float]], dict]]:
    """V-World 2D 데이터 API 건물 레이어 (LT_C_SPBD).

    ✅ 2026-08-15 실검증 완료 — 연산엘지 단지에서 동별 폴리곤·건물명(buld_nm)·
       동 표기(buld_nm_dc)·층수(gro_flo_co)까지 정상 수신 확인 (맥에서 34ms).
    ⚠️ 실서버에서는 api.vworld.kr 아웃바운드가 막혀 있을 수 있다(리버스지오코딩과
       동일 문제, NCP 우회 중). 그 경우 조용히 OSM으로 넘어가고, 디엔에이클라우드가
       아웃바운드를 열어주면 자동으로 이쪽이 우선 사용된다.
    """
    s = get_settings()
    if not getattr(s, "vworld_api_key", None):
        return []
    # ⚠️ size=100 이면 밀집 골목(반경 100m 안 건물 200~400동)에서 100동만 임의 수신 →
    #    바로 옆 건물이 빠져 SVF·폭이 크게 열림(실측80점 골목이 1곳으로 집계된 원인, 2026-09-09).
    #    → size=1000 + 페이지 순회로 전량 수신.
    feats: list = []
    async with httpx.AsyncClient(timeout=12.0) as client:
        for page in range(1, 6):
            r = await client.get(VWORLD_DATA_URL, params={
                "service": "data", "request": "GetFeature", "version": "2.0",
                "data": VWORLD_BUILDING_LAYER, "key": s.vworld_api_key,
                "geomFilter": f"POINT({lon} {lat})", "buffer": str(SEARCH_RADIUS_M),
                "format": "json", "size": "1000", "page": str(page), "geometry": "true",
                "attribute": "true", "crs": "EPSG:4326",
            })
            r.raise_for_status()
            chunk = (r.json().get("response", {}).get("result", {})
                     .get("featureCollection", {}).get("features") or [])
            feats.extend(chunk)
            if len(chunk) < 1000:
                break
        out = []
        for f in feats:
            g = f.get("geometry") or {}
            props = f.get("properties") or {}
            coords = g.get("coordinates") or []
            # Polygon → [외곽 ring, 구멍...], MultiPolygon → [[외곽 ring, ...], ...]
            outer_rings = [coords[0]] if g.get("type") == "Polygon" else [
                c[0] for c in coords if c
            ]
            for ring in outer_rings:
                if isinstance(ring, list) and len(ring) >= 4:
                    out.append((
                        [_to_local_m(p[1], p[0], lat, lon) for p in ring], props,
                    ))
        await _fill_floors_from_register_many([p for _, p in out])
        return out


def _collect_neighbors(
    rings: list[tuple[list[tuple[float, float]], dict]],
    home_ring: list[tuple[float, float]] | None,
    max_n: int = MAX_NEIGHBORS,
    default_floors: int | None = None,
) -> list[Neighbor]:
    """이웃 건물들의 방위·각도폭·거리·높이 — 차폐 계산 재료 (2026-08-15).

    층수를 아는 건물만 넣는다. 높이를 모르는 건물을 임의 높이로 넣으면
    그림자를 지어내는 셈이라, 모르면 뺀다 (근거 없는 값 금지 원칙).
    """
    out: list[Neighbor] = []
    for ring, props in rings:
        if ring is home_ring or len(ring) < 4:
            continue
        H = _height_m_from_props(props, default_floors=default_floors)
        if H is None:
            continue              # 높이 결측(태그·층수 없음) → 그림자 지어내기 금지
        try:
            floors = int(props.get("gro_flo_co") or props.get("building:levels") or 0)
        except (TypeError, ValueError):
            floors = 0
        dist = _dist_to_ring(0.0, 0.0, ring)
        if dist < 1.0:
            continue                     # 사실상 같은 건물
        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)
        center_az = _azimuth_of(cx, cy)
        # 각도 반폭: 꼭짓점들의 방위각이 중심에서 최대 얼마나 벗어나는가
        half = 0.0
        for x, y in ring:
            d = abs(((_azimuth_of(x, y) - center_az + 180) % 360) - 180)
            half = max(half, min(d, 90.0))
        label = str(props.get("buld_nm_dc") or props.get("buld_nm")
                    or props.get("name") or "이웃 건물")
        out.append(Neighbor(
            az_deg=round(center_az, 1), half_deg=round(half, 1),
            dist_m=round(dist, 1), height_m=round(H, 1),
            label=f"{label}({floors}층)" if floors > 0 else f"{label}({round(H)}m)",
        ))
    out.sort(key=lambda n: n.dist_m)
    return out[:max_n]


def shading_factor(
    sun_azimuth_deg: float,
    sun_elevation_deg: float,
    geom: BuildingGeometry | None,
    user_floor: int | None,
) -> tuple[float, str | None]:
    """이웃 건물이 태양을 가리는가 — 실내판 SVF (2026-08-15).

    사용자 층 높이에서 태양 방향을 봤을 때, 그 방위각 안에 있는 이웃 건물의
    꼭대기 앙각이 태양 고도보다 높으면 직달일사가 차단된 것. 배율 0.35(산란만).

    층이 높을수록 앙각이 작아져 그림자를 벗어난다 — 22층은 거의 안 가려지고
    2층은 자주 가려지는 실제 물리가 그대로 나온다.
    """
    if sun_elevation_deg <= 0 or geom is None or not geom.neighbors:
        return 1.0, None
    user_h = max(0, ((user_floor or 1) - 1)) * FLOOR_HEIGHT_M + 1.5   # 창 높이 근사
    for n in geom.neighbors:
        d_az = abs(((sun_azimuth_deg - n.az_deg + 180) % 360) - 180)
        if d_az > n.half_deg + 2.0:      # 태양이 그 건물 방위 밖
            continue
        rise = n.height_m - user_h
        if rise <= 0:
            continue                     # 우리 층이 더 높다
        obstruction = math.degrees(math.atan2(rise, n.dist_m))
        if sun_elevation_deg < obstruction:
            return SHADED_GAIN, f"{n.label}이 햇빛을 가려주는 중"
    return 1.0, None


def facade_solar_gain(
    sun_azimuth_deg: float,
    sun_elevation_deg: float,
    geom: BuildingGeometry | None,
    facing_deg: float | None = None,
) -> tuple[float, str | None]:
    """방위에 따른 일사 취득 배율과 설명 문구.

    반환 1.0 = 방위 미반영(기존과 동일). 0.4(등지는 면) ~ 1.6(정면으로 받는 면).

    · `facing_deg`가 있으면(온보딩에서 사용자가 창 방향을 답한 경우) 그 값을 쓴다.
    · 없으면 판상형의 두 외피 중 **일사를 더 받는 쪽**을 택한다 —
      냉방 없는 취약가구를 보호하는 것이 목적이므로 보수적으로 간다.
    · 타워형이거나 건물을 못 찾으면 1.0(중립).
    """
    if sun_elevation_deg <= 0:
        return 1.0, None

    if facing_deg is not None:
        normal = facing_deg
    elif geom is not None and geom.is_slab:
        # 두 외피 중 태양에 더 가까운 쪽 (보수적)
        da = abs(((sun_azimuth_deg - geom.facade_a_deg + 180) % 360) - 180)
        db = abs(((sun_azimuth_deg - geom.facade_b_deg + 180) % 360) - 180)
        normal = geom.facade_a_deg if da <= db else geom.facade_b_deg
    else:
        return 1.0, None

    diff = abs(((sun_azimuth_deg - normal + 180) % 360) - 180)
    gain = 1.0 + 0.6 * math.cos(math.radians(diff))

    name = ["북", "북동", "동", "남동", "남", "남서", "서", "북서"][
        int(((normal + 22.5) % 360) // 45)
    ]
    note = f"{name}향 외피 — 지금 태양과 {diff:.0f}° 차이"
    return max(0.4, min(1.6, gain)), note
