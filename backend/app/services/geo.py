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

import math
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
    hit = _RINGS_CACHE.get(key)
    if hit is not None:
        ttl = _CACHE_TTL_SEC if hit[1] else _NEG_CACHE_TTL_SEC
        if time.time() - hit[0] < ttl:
            return hit[1] or [], hit[2]

    for name, fn in (("V-World", _rings_from_vworld), ("OSM", _rings_from_osm)):
        try:
            rings = await fn(lat, lon)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"building rings[{name}] failed ({type(e).__name__}): {e}")
            continue
        if rings:
            _RINGS_CACHE[key] = (time.time(), rings, name)
            return rings, name
    _RINGS_CACHE[key] = (time.time(), None, "")
    return [], ""


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
    async with httpx.AsyncClient(timeout=9.0) as client:
        r = await client.get(VWORLD_DATA_URL, params={
            "service": "data", "request": "GetFeature", "version": "2.0",
            "data": VWORLD_BUILDING_LAYER, "key": s.vworld_api_key,
            "geomFilter": f"POINT({lon} {lat})", "buffer": str(SEARCH_RADIUS_M),
            "format": "json", "size": "100", "geometry": "true",
            "attribute": "true", "crs": "EPSG:4326",
        })
        r.raise_for_status()
        feats = (r.json().get("response", {}).get("result", {})
                 .get("featureCollection", {}).get("features") or [])
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
        return out


def _collect_neighbors(
    rings: list[tuple[list[tuple[float, float]], dict]],
    home_ring: list[tuple[float, float]] | None,
) -> list[Neighbor]:
    """이웃 건물들의 방위·각도폭·거리·높이 — 차폐 계산 재료 (2026-08-15).

    층수를 아는 건물만 넣는다. 높이를 모르는 건물을 임의 높이로 넣으면
    그림자를 지어내는 셈이라, 모르면 뺀다 (근거 없는 값 금지 원칙).
    """
    out: list[Neighbor] = []
    for ring, props in rings:
        if ring is home_ring or len(ring) < 4:
            continue
        try:
            floors = int(props.get("gro_flo_co") or props.get("building:levels") or 0)
        except (TypeError, ValueError):
            floors = 0
        if floors <= 0:
            continue
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
            dist_m=round(dist, 1), height_m=round(floors * FLOOR_HEIGHT_M, 1),
            label=f"{label}({floors}층)",
        ))
    out.sort(key=lambda n: n.dist_m)
    return out[:MAX_NEIGHBORS]


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
