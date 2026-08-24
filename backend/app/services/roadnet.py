"""
보행 도로망 공급 — 타일 단위로 한 번만 받아 두고, 그 뒤로는 우리 서버가 준다.

왜 필요한가 (2026-08-24):
  쾌적 경로는 경로를 짤 때마다 **사용자 폰이** 무료 공개 Overpass 서버에서
  그 일대 보행 도로망을 새로 내려받았다. 전 세계가 함께 쓰는 서버라 수 초~수십 초가
  걸리고, 과부하로 죽으면 경로 기능이 통째로 멈췄다. 사용자가 늘면 반드시 터진다.

  이제 앱은 우리 서버만 부른다. 서버는 0.02도(약 2km) 격자 타일 단위로 한 번만
  받아 Redis 에 **영구 보관**한다 → 그 동네 첫 요청만 기다리고 나머지는 전원 즉시.
  부산은 생활권이 겹치므로 며칠이면 주요 지역이 다 채워진다.

응답 형식은 **Overpass 응답 그대로**(`{"elements": [...]}`)다. 앱이 이미 그 모양을
파싱하고 있어, 앱은 주소 한 줄만 바꾸면 된다.

정확도는 그대로다 — 같은 OSM 원본을 쓴다. 바뀌는 건 "누가, 몇 번 받느냐"뿐이다.
"""
from __future__ import annotations

import asyncio
import json
import math
import zlib

import httpx
from loguru import logger

# 타일 한 변 (도). 0.02도 ≈ 위도 2.2km · 부산 경도 1.8km
TILE_DEG = 0.02
# 한 요청이 커버할 수 있는 타일 수 상한 — 폭주 방지 (약 20km × 20km)
MAX_TILES = 120

# ⚠️ 순서가 성능을 좌우한다. 2026-08-24 실서버(lbs-climax-was)에서 직접 확인:
#      overpass-api.de  200  /  overpass.osm.jp  000(도달 불가)
#    지리적으로 가깝다는 이유로 osm.jp 를 1순위에 뒀더니 매 요청마다 거기서 먼저
#    시간을 버렸다. **실측으로 확인된 순서**를 쓴다.
# Overpass 공개 서버는 IP 당 동시 슬롯이 2개다. 타일을 한꺼번에 쏘면 거절당한다.
_SLOTS = asyncio.Semaphore(2)

# 예의상 신원을 밝힌다 — 익명 대량요청은 차단 대상이 된다.
_UA = {"User-Agent": "ClimaX/1.0 (+https://climaxapp.kr) route-tiles"}

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",          # 실서버에서 도달 확인 (200)
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    # overpass.osm.jp 는 뺐다 — 2026-08-24 실서버 로그: SSL 인증서 도메인 불일치로 100% 실패.
]

# 'lite' 는 service(단지 내 도로·주차장 진입로)와 track 을 뺀다 — 용량이 몇 배 줄어든다.
HW_FULL = ("footway|path|pedestrian|steps|living_street|residential|service|"
           "unclassified|tertiary|tertiary_link|secondary|secondary_link|"
           "primary|primary_link|track|cycleway")
HW_LITE = ("footway|path|pedestrian|steps|living_street|residential|"
           "unclassified|tertiary|tertiary_link|secondary|secondary_link|"
           "primary|primary_link|cycleway")


class RoadNetError(Exception):
    pass


def tile_index(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat / TILE_DEG), math.floor(lon / TILE_DEG))


def tiles_for_bbox(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> list[tuple[int, int]]:
    y0, x0 = tile_index(min_lat, min_lon)
    y1, x1 = tile_index(max_lat, max_lon)
    out = [(y, x) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    return out


def tile_bbox(ty: int, tx: int) -> tuple[float, float, float, float]:
    """타일 경계 + 살짝 겹침(30m). 겹치지 않으면 타일 경계에서 길이 끊긴다."""
    pad = 0.0003
    return (ty * TILE_DEG - pad, tx * TILE_DEG - pad,
            (ty + 1) * TILE_DEG + pad, (tx + 1) * TILE_DEG + pad)


def _key(ty: int, tx: int, detail: str) -> str:
    return f"roads:v2:{detail}:{ty}:{tx}"


def _overpass_query(bb: tuple[float, float, float, float], detail: str) -> str:
    b = ",".join(f"{v:.6f}" for v in bb)
    hw = HW_LITE if detail == "lite" else HW_FULL
    return (
        "[out:json][timeout:60];("
        f'way[highway~"^({hw})$"]({b});'
        f'way[leisure~"^(park|garden|playground|recreation_ground|nature_reserve)$"]({b});'
        f'way[landuse~"^(grass|forest|meadow|recreation_ground|cemetery|orchard|'
        f'village_green|reservoir)$"]({b});'
        f'way[natural~"^(water|wood|scrub|grassland|heath|wetland|beach)$"]({b});'
        ");out geom qt;"
    )


async def _fetch_tile(ty: int, tx: int, detail: str) -> list[dict]:
    """한 타일을 Overpass 에서 받는다. 엔드포인트를 순서대로 시도."""
    query = _overpass_query(tile_bbox(ty, tx), detail)
    last: Exception | None = None
    # connect 는 5초 안에 안 붙으면 다음 서버로 — 죽은 서버에서 70초를 버리지 않는다.
    # read 는 넉넉히: 도로망 질의 자체가 수십 초 걸릴 수 있다.
    timeout = httpx.Timeout(connect=5.0, read=70.0, write=20.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in OVERPASS_ENDPOINTS:
            try:
                # ⚠️ 반드시 data= 로 넘겨 httpx 가 URL 인코딩하게 한다.
                #    직접 f"data={query}" 로 만들면 질의 안의 공백("out geom qt;")에서
                #    잘려 Overpass 가 빈 결과를 돌려준다 (2026-08-24 실측).
                async with _SLOTS:
                    resp = await client.post(url, data={"data": query}, headers=_UA)
                if resp.status_code != 200:
                    body = resp.text[:200].replace("\n", " ")
                    last = RoadNetError(f"{url} → HTTP {resp.status_code}")
                    logger.warning("Overpass HTTP {} {} :: {}", resp.status_code, url, body)
                    continue
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001
                    logger.warning("Overpass 비JSON 응답 {} :: {}", url, resp.text[:200])
                    last = RoadNetError(f"{url} → 비JSON 응답")
                    continue
                els = data.get("elements")
                if not isinstance(els, list):
                    last = RoadNetError(f"{url} → 형식 오류")
                    continue
                if not els:
                    # 바다·산 한가운데면 정말 빌 수 있지만 대부분은 질의가 잘못 간 것이다.
                    # 빈 결과를 캐시하면 그 타일이 영원히 비어 버린다 → 실패로 처리.
                    # ★ 원인 추적을 위해 응답 앞부분을 반드시 남긴다 (조용히 넘어가면 못 찾는다).
                    logger.warning("Overpass 빈 결과 {} :: remark={} :: {}",
                                   url, data.get("remark"), resp.text[:300])
                    last = RoadNetError(f"{url} → 빈 결과")
                    continue
                return els
            except Exception as e:  # noqa: BLE001
                last = e
                logger.warning("Overpass 실패 {}: {}", url, e)
    raise RoadNetError(str(last) if last else "모든 Overpass 엔드포인트 실패")


class RoadNetService:
    """타일 캐시 + Overpass 수집."""

    def __init__(self, cache) -> None:   # noqa: ANN001 — CacheService
        self._cache = cache
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, k: str) -> asyncio.Lock:
        # 같은 타일을 여러 사용자가 동시에 요청해도 Overpass 는 한 번만 부른다
        if k not in self._locks:
            self._locks[k] = asyncio.Lock()
        return self._locks[k]

    async def _get_cached(self, k: str) -> list[dict] | None:
        if self._cache is None:
            return None
        try:
            raw = await self._cache._client.get(k)   # noqa: SLF001
        except Exception as e:  # noqa: BLE001
            logger.warning("도로망 캐시 조회 실패: {}", e)
            return None
        if not raw:
            return None
        try:
            return json.loads(zlib.decompress(raw))
        except Exception:  # noqa: BLE001
            return None

    async def _put_cached(self, k: str, els: list[dict]) -> None:
        if self._cache is None:
            return
        try:
            # 압축해서 넣는다 — 도로망 JSON 은 반복이 많아 1/5 아래로 줄어든다.
            # TTL 없음: 도로는 거의 안 바뀐다. 갱신이 필요하면 키 접두사(v1)를 올린다.
            await self._cache._client.set(   # noqa: SLF001
                k, zlib.compress(json.dumps(els, separators=(",", ":")).encode(), 6)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("도로망 캐시 저장 실패: {}", e)

    async def tile(self, ty: int, tx: int, detail: str) -> tuple[list[dict], bool]:
        """한 타일. (elements, 캐시적중여부)"""
        k = _key(ty, tx, detail)
        hit = await self._get_cached(k)
        if hit is not None:
            return hit, True
        async with self._lock(k):
            hit = await self._get_cached(k)      # 락 대기 중에 남이 채웠을 수 있다
            if hit is not None:
                return hit, True
            els = await _fetch_tile(ty, tx, detail)
            if els:                      # 빈 결과는 절대 저장하지 않는다
                await self._put_cached(k, els)
            return els, False

    async def bbox(
        self, min_lat: float, min_lon: float, max_lat: float, max_lon: float,
        detail: str = "lite",
    ) -> dict:
        tl = tiles_for_bbox(min_lat, min_lon, max_lat, max_lon)
        if len(tl) > MAX_TILES:
            raise RoadNetError(
                f"요청 범위가 너무 넓습니다 (타일 {len(tl)}개 > {MAX_TILES}개)."
            )
        results = await asyncio.gather(
            *(self.tile(ty, tx, detail) for ty, tx in tl), return_exceptions=True
        )
        elements: list[dict] = []
        seen: set = set()
        hits = 0
        failed = 0
        for r in results:
            if isinstance(r, Exception):
                failed += 1
                continue
            els, hit = r
            if hit:
                hits += 1
            for e in els:
                # 타일이 겹치므로 같은 way 가 여러 번 온다 — id 로 한 번만 담는다
                eid = (e.get("type"), e.get("id"))
                if eid in seen:
                    continue
                seen.add(eid)
                elements.append(e)
        if failed and not elements:
            raise RoadNetError("도로망을 받지 못했습니다 (전 타일 실패).")
        return {
            "elements": elements,
            "meta": {
                "tiles": len(tl), "cache_hits": hits, "failed": failed,
                "detail": detail, "count": len(elements),
            },
        }
