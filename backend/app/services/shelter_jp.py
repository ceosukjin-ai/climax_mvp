"""일본 — 더위를 피할 곳 (쿨링 셸터) (2026-09-25).

두 층으로 나눠 보여주고, **절대 섞어 부르지 않는다.**

1) 公式 — 지자체가 지정·공개한 곳 (DB `cooling_shelter`, scripts/load_shelters_jp.py 로 적재)
   · kind='designated'  : 指定暑熱避難施設(クーリングシェルター) — 기후변화적응법상 지정.
   · kind='coolshare'   : TOKYOクールシェアスポット 등 지자체가 공개한 쉼터 목록.
   좌표를 공개한 지자체만 들어간다. 없는 곳을 지어내지 않는다.

2) 冷房のある場所(目安) — OSM 의 도서관·공민관·구청·백화점·쇼핑몰·편의점.
   **지정 셸터가 아니다.** 냉방이 있을 **가능성이 높은** 곳일 뿐이고, 개방 여부·시간은 모른다.
   화면에 반드시 "公式ではありません" 을 함께 띄운다.

환경성은 전국 셸터 좌표를 한데 모아 주지 않는다(지자체 링크 모음만). 그래서 공식 층은
지자체 오픈데이터가 있는 곳부터 채운다 — 처음엔 도쿄도 카탈로그.
출처표기: 공식 = 각 지자체(CC BY 등, 행마다 license/source 보관) · 目安 = © OpenStreetMap contributors.
"""
from __future__ import annotations

import math
import time
from typing import Any

import httpx
from loguru import logger

from app.services.roadnet import OVERPASS_ENDPOINTS, _SLOTS, _UA

MAX_RADIUS_M = 2000
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL_S = 1800.0

# OSM 目安 — 종류 → (Overpass 필터, 표시 분류)
OSM_KINDS = [
    ('amenity~"^(library|community_centre|townhall)$"', "public"),
    ('shop~"^(department_store|mall)$"', "store"),
    ('shop="convenience"', "konbini"),
]
KIND_LABEL = {
    "designated": {"ja": "クーリングシェルター（公式）", "en": "Cooling shelter (official)", "ko": "쿨링 셸터 (공식)"},
    "coolshare":  {"ja": "クールシェアスポット（公式）", "en": "Cool-share spot (official)", "ko": "쿨셰어 스폿 (공식)"},
    "public":     {"ja": "公共施設", "en": "Public building", "ko": "공공시설"},
    "store":      {"ja": "商業施設", "en": "Shopping centre", "ko": "상업시설"},
    "konbini":    {"ja": "コンビニ", "en": "Convenience store", "ko": "편의점"},
}
NOTE = {
    "ja": "「公式」は自治体が公開した施設です。開放日時は各施設・自治体の案内をご確認ください。"
          "それ以外は冷房がある可能性が高い場所の目安で、公式の避難施設ではありません。",
    "en": "“Official” places are published by the city; check each place for opening hours. "
          "Others are only likely air-conditioned places, not official shelters.",
    "ko": "「공식」은 지자체가 공개한 시설입니다. 개방 시간은 각 시설·지자체 안내를 확인하세요. "
          "그 밖은 냉방이 있을 가능성이 높은 곳을 참고로 보여드리는 것이며, 공식 쉼터가 아닙니다.",
}


def _hav(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


async def _official(lat: float, lon: float, radius: int) -> list[dict[str, Any]]:
    try:
        from app.services.skyline import _get_pool
        pool = await _get_pool()
    except Exception:  # noqa: BLE001
        return []
    if pool is None:
        return []
    dlat = radius / 111_320.0
    dlon = radius / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
    try:
        async with pool.acquire() as c:
            if not await c.fetchval("SELECT to_regclass('cooling_shelter') IS NOT NULL"):
                return []
            rows = await c.fetch(
                "SELECT name, addr, lat, lon, kind, hours, src, license, source_url "
                "FROM cooling_shelter WHERE lat BETWEEN $1 AND $2 AND lon BETWEEN $3 AND $4",
                lat - dlat, lat + dlat, lon - dlon, lon + dlon)
    except Exception as e:  # noqa: BLE001
        logger.warning("[shelter] DB 실패: {}", e)
        return []
    out = []
    for r in rows:
        d = _hav(lat, lon, r["lat"], r["lon"])
        if d <= radius:
            out.append({"name": r["name"], "addr": r["addr"], "lat": round(r["lat"], 6),
                        "lon": round(r["lon"], 6), "kind": r["kind"], "official": True,
                        "hours": r["hours"], "src": r["src"], "license": r["license"],
                        "source_url": r["source_url"], "meters": round(d)})
    return out


async def _osm(lat: float, lon: float, radius: int, lang: str) -> list[dict[str, Any]]:
    parts = []
    for f, _k in OSM_KINDS:
        parts.append(f'node[{f}](around:{radius},{lat:.6f},{lon:.6f});')
        parts.append(f'way[{f}](around:{radius},{lat:.6f},{lon:.6f});')
    q = "[out:json][timeout:40];(" + "".join(parts) + ");out center tags qt 300;"
    els = None
    timeout = httpx.Timeout(connect=5.0, read=45.0, write=15.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in OVERPASS_ENDPOINTS:
            try:
                async with _SLOTS:
                    resp = await client.post(url, data={"data": q}, headers=_UA)
                if resp.status_code == 200 and isinstance(resp.json().get("elements"), list):
                    els = resp.json()["elements"]
                    break
                logger.warning("[shelter] {} HTTP {}", url, resp.status_code)
            except Exception as e:  # noqa: BLE001
                logger.warning("[shelter] {} 실패: {}", url, e)
    out = []
    for el in els or []:
        t = el.get("tags") or {}
        la = el.get("lat") or (el.get("center") or {}).get("lat")
        lo = el.get("lon") or (el.get("center") or {}).get("lon")
        if la is None or lo is None:
            continue
        a, s = t.get("amenity", ""), t.get("shop", "")
        kind = ("public" if a in ("library", "community_centre", "townhall")
                else "store" if s in ("department_store", "mall")
                else "konbini" if s == "convenience" else None)
        if kind is None:
            continue
        name = (t.get(f"name:{lang}") or t.get("name") or t.get("brand") or "").strip()
        if not name:
            name = KIND_LABEL[kind].get(lang, KIND_LABEL[kind]["ja"])
        d = _hav(lat, lon, float(la), float(lo))
        if d > radius:
            continue
        out.append({"name": name, "addr": None, "lat": round(float(la), 6), "lon": round(float(lo), 6),
                    "kind": kind, "official": False, "hours": t.get("opening_hours"),
                    "meters": round(d)})
    return out


async def nearby(lat: float, lon: float, radius: int = 800, lang: str = "ja") -> dict:
    radius = max(200, min(int(radius), MAX_RADIUS_M))
    key = f"{round(lat, 3)}:{round(lon, 3)}:{radius}:{lang}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    off = await _official(lat, lon, radius)
    osm = await _osm(lat, lon, radius, lang)
    # 공식과 같은 건물(40 m 안)인 OSM 점은 뺀다 — 같은 곳이 두 번 나오면 공식이 묻힌다.
    osm = [o for o in osm if not any(_hav(o["lat"], o["lon"], f["lat"], f["lon"]) < 40 for f in off)]
    off.sort(key=lambda x: x["meters"])
    # 目安 은 공공시설·상업시설을 편의점보다 앞에 — 오래 머물 수 있고 앉을 곳이 있다.
    rank = {"public": 0, "store": 1, "konbini": 2}
    osm.sort(key=lambda x: (x["meters"] > 300, rank[x["kind"]], x["meters"]))
    res = {"official": off[:30], "candidates": osm[:30],
           "labels": {k: v.get(lang, v["ja"]) for k, v in KIND_LABEL.items()},
           "note": NOTE.get(lang, NOTE["ja"])}
    if len(_CACHE) > 300:
        for kk in list(_CACHE)[:150]:
            _CACHE.pop(kk, None)
    _CACHE[key] = (time.time(), res)
    return res
