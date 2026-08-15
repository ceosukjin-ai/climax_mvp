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
SEARCH_RADIUS_M = 40


@dataclass
class BuildingGeometry:
    long_axis_deg: float      # 건물 장축 방위각 (0=북, 90=동)
    facade_a_deg: float       # 주 외피 법선 ① (장축 +90°)
    facade_b_deg: float       # 주 외피 법선 ② (반대편)
    elongation: float         # 장축/단축 비 — 1에 가까우면 타워형
    is_slab: bool             # 판상형으로 볼 수 있는가
    source: str               # "osm"


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
    # 리버스지오코딩과 같은 방식 — 국내 공식 데이터(V-World)를 먼저, 실패하면 OSM.
    for name, fn in (("V-World", _rings_from_vworld), ("OSM", _rings_from_osm)):
        try:
            rings = await fn(lat, lon)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"building_geometry[{name}] failed ({type(e).__name__}): {e}")
            continue
        best = _pick_ring(rings, name_hint, floors_hint)
        if best is None:
            continue
        axis, elong = _principal_axis(best)
        result = BuildingGeometry(
            long_axis_deg=round(axis, 1),
            facade_a_deg=round((axis + 90.0) % 360.0, 1),
            facade_b_deg=round((axis + 270.0) % 360.0, 1),
            elongation=round(elong, 2),
            is_slab=elong >= MIN_ELONGATION,
            source=name,
        )
        break

    _CACHE[key] = (time.time(), result)
    return result


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
