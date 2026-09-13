#!/usr/bin/env python3
"""위성 수관고 래스터(B) × 가로수 공공데이터(A) 교차검증 (2026-09-12).

왜: A 는 지자체 등록 '가로수길'만, B 는 위성 추정이다. 둘은 완전히 독립적인 출처다.
    부산은 두 자료가 겹치므로, **A 가 나무라 한 곳에서 B 도 높이를 보는지**로 서로를 검증할 수 있다.
    이게 성립해야 A 가 없는 일본·전세계에서 B 를 믿고 쓸 수 있다.

읽기: ENVI 원시 바이너리 + numpy.memmap. rasterio/GDAL 불필요 — 컨테이너에 의존성을 안 넣는다.

  docker exec climax-api python3 /tmp/canopy_check.py
"""
from __future__ import annotations
import asyncio, sys
import numpy as np
sys.path.insert(0, "/app")

IMG = "/repo/data/canopy/busan_canopy.img"
HDR = "/repo/data/canopy/busan_canopy.hdr"


def load():
    h = {}
    for line in open(HDR, encoding="utf-8", errors="replace"):
        if "=" in line:
            k, v = line.split("=", 1)
            h[k.strip().lower()] = v.strip()
    ns, nl = int(h["samples"]), int(h["lines"])
    # map info = {Geographic Lat/Lon, 1, 1, ulx, uly, xres, yres, ...}
    mi = [p.strip() for p in h["map info"].strip("{} ").split(",")]
    ulx, uly, xres, yres = float(mi[3]), float(mi[4]), float(mi[5]), float(mi[6])
    a = np.memmap(IMG, dtype=np.uint8, mode="r", shape=(nl, ns))
    return a, ulx, uly, xres, yres, ns, nl


class Canopy:
    def __init__(self):
        self.a, self.ulx, self.uly, self.xr, self.yr, self.ns, self.nl = load()

    def at(self, lat, lon):
        c = int((lon - self.ulx) / self.xr)
        r = int((self.uly - lat) / self.yr)
        if 0 <= r < self.nl and 0 <= c < self.ns:
            return float(self.a[r, c])
        return None

    def maxwin(self, lat, lon, win_m=20.0):
        """반경 win_m 안의 최대 수관고 — 차폐는 '가장 높은 나무'가 결정한다."""
        dlat = win_m / 111320.0
        dlon = win_m / (111320.0 * np.cos(np.radians(lat)))
        r0 = max(0, int((self.uly - lat - dlat) / self.yr))
        r1 = min(self.nl, int((self.uly - lat + dlat) / self.yr) + 1)
        c0 = max(0, int((lon - dlon - self.ulx) / self.xr))
        c1 = min(self.ns, int((lon + dlon - self.ulx) / self.xr) + 1)
        if r1 <= r0 or c1 <= c0:
            return None
        return float(self.a[r0:r1, c0:c1].max())


def stat(name, vals):
    v = np.array([x for x in vals if x is not None], dtype=float)
    if not len(v):
        print(f"{name}: 표본 없음"); return
    nz = (v > 0).mean() * 100
    print(f"{name:<34} n={len(v):<5} 평균 {v.mean():5.2f}m  중앙 {np.median(v):5.2f}m  "
          f"3m초과 {(v>3).mean()*100:4.0f}%  0초과 {nz:4.0f}%")


TREE_SQL = """
SELECT ST_Y(geom) la, ST_X(geom) lo FROM tree_point
 WHERE src = 'kr-gov'
   AND geom && ST_MakeEnvelope(128.80, 35.05, 129.25, 35.35, 4326)
 ORDER BY random() LIMIT 1500
"""
# 대조군 — 가로수가 없는 부산 실측좌표(반경 60m 내 나무 0그루)
CTRL_SQL = """
SELECT m.lat::float8 la, m.lon::float8 lo
  FROM (SELECT DISTINCT round(lat::numeric,5) lat, round(lon::numeric,5) lon
          FROM measurement
         WHERE indoor = FALSE AND lat BETWEEN 35.05 AND 35.35 AND lon BETWEEN 128.80 AND 129.25
         LIMIT 3000) m
 WHERE NOT EXISTS (
       SELECT 1 FROM tree_point t
        WHERE t.geom && ST_MakeEnvelope(m.lon::float8-0.00066, m.lat::float8-0.00054,
                                        m.lon::float8+0.00066, m.lat::float8+0.00054, 4326))
 LIMIT 1500
"""
NEAR_SQL = """
SELECT m.lat::float8 la, m.lon::float8 lo
  FROM (SELECT DISTINCT round(lat::numeric,5) lat, round(lon::numeric,5) lon
          FROM measurement
         WHERE indoor = FALSE AND lat BETWEEN 35.05 AND 35.35 AND lon BETWEEN 128.80 AND 129.25
         LIMIT 3000) m
 WHERE EXISTS (
       SELECT 1 FROM tree_point t
        WHERE t.geom && ST_MakeEnvelope(m.lon::float8-0.00024, m.lat::float8-0.00020,
                                        m.lon::float8+0.00024, m.lat::float8+0.00020, 4326))
 LIMIT 1500
"""


async def main():
    cv = Canopy()
    print(f"래스터 {cv.ns}×{cv.nl}  좌상단 {cv.ulx},{cv.uly}  화소 {cv.xr}°(약 {cv.xr*111320*np.cos(np.radians(35.2)):.0f}m)")
    print(f"전체   평균 {cv.a.mean():.2f}m  최대 {cv.a.max():.0f}m  "
          f"0초과 화소 {(np.asarray(cv.a[::7, ::7]) > 0).mean()*100:.0f}%\n")

    from app.services.skyline import _get_pool
    pool = await _get_pool()
    async with pool.acquire() as c:
        trees = await c.fetch(TREE_SQL)
        near = await c.fetch(NEAR_SQL)
        ctrl = await c.fetch(CTRL_SQL)

    print("── 화소 바로 위(점 대 점) ──")
    stat("A 가로수 좌표", [cv.at(float(r["la"]), float(r["lo"])) for r in trees])
    stat("실측좌표 · 나무 있음(20m)", [cv.at(float(r["la"]), float(r["lo"])) for r in near])
    stat("실측좌표 · 나무 없음(60m)", [cv.at(float(r["la"]), float(r["lo"])) for r in ctrl])
    print("\n── 반경 20m 최대(차폐 판정에 쓰일 값) ──")
    stat("A 가로수 좌표", [cv.maxwin(float(r["la"]), float(r["lo"])) for r in trees])
    stat("실측좌표 · 나무 있음(20m)", [cv.maxwin(float(r["la"]), float(r["lo"])) for r in near])
    stat("실측좌표 · 나무 없음(60m)", [cv.maxwin(float(r["la"]), float(r["lo"])) for r in ctrl])
    print()
    print("판정: '나무 있음' 이 '나무 없음' 보다 뚜렷이 높아야 두 자료가 서로를 확인한 것이다.")
    print("      차이가 없으면 둘 중 하나가 틀렸거나 좌표 정렬이 어긋난 것이다.")


if __name__ == "__main__":
    asyncio.run(main())
