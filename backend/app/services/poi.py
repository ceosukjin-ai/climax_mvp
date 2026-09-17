"""주변 장소(식당·카페·관광지) 조회 — Overpass (2026-09-13).

왜: 그늘 경로는 이미 A→B 로 동작하지만, 관광객은 **가려는 가게가 지도 어디인지 모른다.**
목적지를 찍을 방법이 없어서 기능이 닿지 않았다. 내 좌표 주변에 뭐가 있는지 보여주고,
하나 고르면 그 지점을 목적지로 넘긴다. 순위는 거리순이다 — "시원한 순"으로 정렬하지 않는다.
사람은 갈 집을 먼저 정하고, 쾌적함은 **그 길을 어떻게 갈지**의 문제이기 때문이다.

도로망과 같은 Overpass 통로를 쓴다(같은 엔드포인트·동시성 제한·User-Agent).
새 API 계약도 추가 비용도 없다. 대신 OSM 은 평점·사진·영업시간이 거의 없다 —
'맛집 발견'은 우리 역할이 아니고, 정해진 목적지까지 시원하게 데려다주는 것이 역할이다.

출처표기: © OpenStreetMap contributors (ODbL).
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import httpx
from loguru import logger

from app.services.roadnet import OVERPASS_ENDPOINTS, RoadNetError, _SLOTS, _UA

# 종류 → (Overpass 필터, 표시용 분류)
KINDS: dict[str, tuple[str, str]] = {
    "restaurant":  ('amenity~"^(restaurant|fast_food|bbq)$"', "food"),
    "cafe":        ('amenity~"^(cafe|ice_cream)$"', "cafe"),
    "bar":         ('amenity~"^(bar|pub)$"', "bar"),
    "attraction":  ('tourism~"^(attraction|museum|viewpoint|artwork|gallery|zoo|aquarium)$"', "see"),
    "shrine":      ('amenity="place_of_worship"', "see"),
    "park":        ('leisure~"^(park|garden)$"', "see"),
    "convenience": ('shop~"^(convenience|supermarket|department_store)$"', "shop"),
    "toilets":     ('amenity="toilets"', "util"),
    "water":       ('amenity="drinking_water"', "util"),
    # 자판기 (2026-09-18, 일본). 여름 보행에서 수분 보급 지점이다. 일본은 OSM 매핑이 촘촘하다.
    # 음료 자판기만 — 담배·잡화 자판기가 섞이면 목록이 쓸모없어진다.
    # ⚠️ 필터 문자열에 `][` 가 들어간다. _query 가 node[{필터}] 로 감싸므로 태그 두 개를 이렇게 잇는다.
    "vending":     ('amenity="vending_machine"][vending~"drink|water|beverage"', "util"),
}

# 이름이 없어도 쓸모 있는 종류 (2026-09-18).
# 지금까지 이름 없는 점을 전부 버렸는데, 화장실·식수대·자판기는 **이름이 없는 게 정상**이다.
# 일본 공원 식수대·자판기에 이름이 붙은 경우는 거의 없어서 사실상 하나도 안 나오고 있었다.
# 목적지로 고르는 가게와 달리 이들은 "가는 길의 지점"이라 이름이 필요 없다.
NONAME: dict[str, dict[str, str]] = {
    "toilets":        {"ja": "トイレ", "en": "Toilet", "ko": "화장실"},
    "drinking_water": {"ja": "水飲み場", "en": "Drinking water", "ko": "식수대"},
    "vending_machine": {"ja": "自販機", "en": "Vending machine", "ko": "자판기"},
}
DEFAULT_KINDS = ("restaurant", "cafe", "attraction")
MAX_RADIUS_M = 1500
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_TTL_S = 1800.0
_CACHE_MAX = 400


def _haversine(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _query(lat: float, lon: float, radius: int, kinds: tuple[str, ...]) -> str:
    parts = []
    for k in kinds:
        f = KINDS.get(k)
        if not f:
            continue
        # node 와 way 둘 다 — 건물로 그려진 가게가 많다. way 는 중심점만 받는다(out center).
        parts.append(f'node[{f[0]}](around:{radius},{lat:.6f},{lon:.6f});')
        parts.append(f'way[{f[0]}](around:{radius},{lat:.6f},{lon:.6f});')
    return "[out:json][timeout:40];(" + "".join(parts) + ");out center tags qt 200;"


async def near(lat: float, lon: float, radius: int = 600,
               kinds: tuple[str, ...] = DEFAULT_KINDS,
               lang: str = "ja") -> list[dict[str, Any]]:
    """반경 내 장소를 가까운 순으로. 실패하면 빈 목록 — 경로 기능을 막지 않는다."""
    radius = max(100, min(int(radius), MAX_RADIUS_M))
    key = f"{round(lat, 3)}:{round(lon, 3)}:{radius}:{','.join(sorted(kinds))}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]

    q = _query(lat, lon, radius, kinds)
    timeout = httpx.Timeout(connect=5.0, read=45.0, write=15.0, pool=5.0)
    els: list[dict] | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in OVERPASS_ENDPOINTS:
            try:
                async with _SLOTS:
                    resp = await client.post(url, data={"data": q}, headers=_UA)
                if resp.status_code != 200:
                    logger.warning("[poi] {} HTTP {}", url, resp.status_code)
                    continue
                data = resp.json()
                if isinstance(data.get("elements"), list):
                    els = data["elements"]
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning("[poi] {} 실패: {}", url, e)
    if els is None:
        return []

    kind_of = {}
    for k in kinds:
        if k in KINDS:
            kind_of[k] = KINDS[k][1]

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in els:
        t = el.get("tags") or {}
        la = el.get("lat") or (el.get("center") or {}).get("lat")
        lo = el.get("lon") or (el.get("center") or {}).get("lon")
        if la is None or lo is None:
            continue
        name = (t.get(f"name:{lang}") or t.get("name")
                or t.get("name:en") or t.get("name:ja") or "").strip()
        if not name:
            # 화장실·식수대·자판기는 이름이 없는 게 정상 — 기본 이름을 준다 (2026-09-18).
            name = (NONAME.get(t.get("amenity") or "", {}) or {}).get(lang, "")
            if not name:
                continue                   # 그 밖의 이름 없는 점은 목적지로 고를 수 없다
        k = ("food" if t.get("amenity") in ("restaurant", "fast_food", "bbq")
             else "cafe" if t.get("amenity") in ("cafe", "ice_cream")
             else "bar" if t.get("amenity") in ("bar", "pub")
             else "shop" if t.get("shop") else
             "util" if t.get("amenity") in ("toilets", "drinking_water", "vending_machine")
             else "see")
        d = _haversine(lat, lon, float(la), float(lo))
        if d > radius:
            continue
        dedup = f"{name}:{round(float(la), 4)}:{round(float(lo), 4)}"
        if dedup in seen:
            continue
        seen.add(dedup)
        out.append({
            "name": name,
            "name_en": (t.get("name:en") or "").strip() or None,
            "lat": round(float(la), 6),
            "lon": round(float(lo), 6),
            "kind": k,
            "cuisine": (t.get("cuisine") or "").split(";")[0] or None,
            "meters": round(d),
        })
    out.sort(key=lambda x: x["meters"])
    out = out[:60]
    if len(_CACHE) > _CACHE_MAX:
        for kk in list(_CACHE)[: _CACHE_MAX // 2]:
            _CACHE.pop(kk, None)
    _CACHE[key] = (time.time(), out)
    return out

# ── 경로를 따라가며 찾기 (2026-09-13) ────────────────────────
# 왜: 출발점 주변만 보면 가는 방향과 무관한 가게가 섞인다. 관광객이 원하는 건
#     "지금 가는 길에 뭐가 있나" 다. 경로 폴리라인에서 일정 거리 안쪽만 남기고,
#     **가는 순서대로** 정렬한다. 돌아가는 거리(detour)도 같이 준다.

def _seg_dist_m(px, py, ax, ay, bx, by):
    """점(px,py)에서 선분 ab 까지 거리(m). 좌표는 이미 미터 평면으로 변환된 값."""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 <= 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    cx, cy = ax + vx * t, ay + vy * t
    return math.hypot(px - cx, py - cy), t


async def along(path: list[tuple[float, float]], radius: int = 150,
                kinds: tuple[str, ...] = DEFAULT_KINDS,
                lang: str = "ja") -> list[dict[str, Any]]:
    """경로(폴리라인) 주변 장소. 반환 순서는 **출발지에서 가까운 순**(가는 순서)."""
    if len(path) < 2:
        return []
    radius = max(50, min(int(radius), 400))
    lats = [p[0] for p in path]; lons = [p[1] for p in path]
    clat = sum(lats) / len(lats); clon = sum(lons) / len(lons)

    # 경로 전체를 덮는 원 하나로 Overpass 를 한 번만 부른다 — 호출 수를 늘리지 않는다.
    mlat, mlon = 111320.0, 111320.0 * math.cos(math.radians(clat))
    span = max(max(lats) - min(lats), 0.0) * mlat / 2.0
    span = max(span, (max(lons) - min(lons)) * mlon / 2.0)
    items = await near(clat, clon, int(min(span + radius + 100, MAX_RADIUS_M)), kinds, lang=lang)
    if not items:
        return []

    # 미터 평면으로 옮겨 선분까지의 거리를 잰다
    pts = [((la - clat) * mlat, (lo - clon) * mlon) for la, lo in path]
    seglen = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
              for i in range(len(pts) - 1)]
    cum = [0.0]
    for L in seglen:
        cum.append(cum[-1] + L)
    total = cum[-1] or 1.0

    out = []
    for it in items:
        py = (it["lat"] - clat) * mlat
        px = (it["lon"] - clon) * mlon
        best_d, best_s = 1e9, 0.0
        for i in range(len(pts) - 1):
            d, t = _seg_dist_m(px, py, pts[i][1], pts[i][0], pts[i + 1][1], pts[i + 1][0])
            if d < best_d:
                best_d, best_s = d, cum[i] + seglen[i] * t
        if best_d > radius:
            continue
        o = dict(it)
        o["detour_m"] = round(best_d)          # 경로에서 벗어나는 거리
        o["at_m"] = round(best_s)              # 출발지로부터 경로상 위치
        o["at_pct"] = round(best_s / total * 100)
        out.append(o)
    out.sort(key=lambda x: x["at_m"])
    return out[:40]
