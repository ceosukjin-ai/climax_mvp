"""
무더위쉼터 — 전국 (행정안전부 safetydata.go.kr).

왜 서버로 옮겼나 (2026-08-24):
  쾌적 경로 데모 HTML 안에 **부산 쉼터 1,688곳이 하드코딩**돼 있었다. 앱을 스토어에
  올리면 서울·대구 사용자가 받는데, 그들에게는 "가장 가까운 무더위쉼터로" 버튼이
  통째로 먹통이 된다. 데모용 임시 데이터가 그대로 출시로 갈 뻔했다.

  전국 61,017곳(2026-08-05 수집)을 서버에 두고 위치 기준으로 가까운 것만 내려준다.
  앱은 가벼워지고, 갱신도 서버 파일만 갈아끼우면 된다.

데이터: `app/data/shelters_kr.json.gz` — [이름, 위도, 경도, 유형, 평일시작, 평일종료, 주소]
  · 유형(FCLTY_TY): 001 실내 · 002 실외(그늘막 등) · 003 경로당·마을회관 · 004 민간시설
  · 갱신: 행안부 오픈API 재수집 → 이 파일 교체 → 배포
"""
from __future__ import annotations

import gzip
import json
import math
import pathlib
import threading

from loguru import logger

DATA_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "shelters_kr.json.gz"

# 격자 한 변(도). 0.05° ≈ 5.5km — 반경 몇 km 조회에 인접 칸 몇 개만 보면 된다.
CELL = 0.05

SHELTER_TYPES = {
    "001": "실내쉼터",
    "002": "야외쉼터",
    "003": "경로당·마을회관",
    "004": "민간시설",
}

_lock = threading.Lock()
_rows: list | None = None
_index: dict[tuple[int, int], list[int]] | None = None


def _load() -> None:
    """첫 호출 때 한 번만 읽어 격자 색인을 만든다."""
    global _rows, _index
    if _rows is not None:
        return
    with _lock:
        if _rows is not None:
            return
        if not DATA_PATH.exists():
            logger.warning("쉼터 데이터 없음: {}", DATA_PATH)
            _rows, _index = [], {}
            return
        with gzip.open(DATA_PATH, "rt", encoding="utf-8") as fp:
            rows = json.load(fp)
        idx: dict[tuple[int, int], list[int]] = {}
        for i, r in enumerate(rows):
            key = (int(math.floor(r[1] / CELL)), int(math.floor(r[2] / CELL)))
            idx.setdefault(key, []).append(i)
        _rows, _index = rows, idx
        logger.info("무더위쉼터 {}곳 적재 (격자 {}칸)", len(rows), len(idx))


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _hhmm(v: str) -> str:
    return f"{v[:2]}:{v[2:]}" if len(v) == 4 else ""


def nearby(lat: float, lon: float, radius_m: float = 3000.0,
           limit: int = 200) -> list[dict]:
    """반경 안의 쉼터를 가까운 순으로. 없으면 빈 목록."""
    _load()
    if not _rows:
        return []
    # 반경을 덮는 격자 칸만 훑는다 (전국 6만 건 전수 계산을 피한다)
    span_lat = radius_m / 111_000.0
    span_lon = radius_m / (111_000.0 * max(0.2, math.cos(math.radians(lat))))
    y0 = int(math.floor((lat - span_lat) / CELL))
    y1 = int(math.floor((lat + span_lat) / CELL))
    x0 = int(math.floor((lon - span_lon) / CELL))
    x1 = int(math.floor((lon + span_lon) / CELL))

    found: list[tuple[float, dict]] = []
    assert _index is not None
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            for i in _index.get((y, x), ()):
                r = _rows[i]
                d = _haversine_m(lat, lon, r[1], r[2])
                if d > radius_m:
                    continue
                found.append((d, {
                    "name": r[0], "lat": r[1], "lon": r[2],
                    "type": r[3], "type_name": SHELTER_TYPES.get(r[3], "쉼터"),
                    "open": _hhmm(r[4]), "close": _hhmm(r[5]),
                    "address": r[6], "distance_m": int(d),
                }))
    found.sort(key=lambda t: t[0])
    return [s for _, s in found[:limit]]


def count() -> int:
    _load()
    return len(_rows or [])
