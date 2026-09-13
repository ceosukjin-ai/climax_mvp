#!/usr/bin/env python3
"""그늘 판정이 위치 오차에 얼마나 민감한가 (2026-09-13).

왜:
  그늘 21지점의 태양고도 중앙값이 64도다. 이 고도에서 3층 건물이 만드는 그늘은
  반경 3.7 m 다. GPS 오차는 5~10 m. 즉 그늘 여부가 위치 오차 안에서 결정될 수
  있다. 그렇다면 폴리곤이나 층수를 아무리 고쳐도 못 맞힌다.

  각 지점 주위를 원판으로 훑어서, 그 근처에서 '그늘'이 나오는 위치가 얼마나
  되는지 센다.

    0 %        : 이 근방에 그늘을 만들 건물이 아예 없다. 건물 데이터/기하 문제.
    1 ~ 40 %   : 그늘은 존재하는데 자리를 못 짚는다. 위치 정확도 문제.
    40 % 이상  : 넓게 그늘인데도 놓쳤다. 판정 로직 문제.

쓰는 법 (컨테이너 안):
  docker exec -i climax-api python3 /tmp/shade_gps_probe.py /tmp/pts80.csv
  옵션: --radius 12  --rings 3  --spokes 8   (기본값)
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import math
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="/tmp/pts80.csv")
    ap.add_argument("--radius", type=float, default=12.0, help="훑을 반경 m")
    ap.add_argument("--rings", type=int, default=3)
    ap.add_argument("--spokes", type=int, default=8)
    ap.add_argument("--only-shade", action="store_true",
                    help="실측 그늘 지점만 본다")
    a = ap.parse_args()

    from vpti_core.solar import estimate_solar
    from app.services import geo

    rows = list(csv.DictReader(open(a.path, encoding="utf-8-sig")))
    if a.only_shade:
        rows = [r for r in rows if str(r["볕"]).strip() != "1"]

    # 중심 + (rings x spokes) 개 표본
    offsets = [(0.0, 0.0)]
    for i in range(1, a.rings + 1):
        rad = a.radius * i / a.rings
        for j in range(a.spokes):
            th = 2 * math.pi * j / a.spokes
            offsets.append((rad * math.cos(th), rad * math.sin(th)))

    print(f"\n지점 {len(rows)}개 · 지점마다 {len(offsets)}표본 "
          f"(반경 {a.radius:.0f} m)\n")
    print(f"  {'지점':<16}{'실측':<6}{'중심':<6}{'그늘비율':>9}   태양고도")

    buckets = {"0%": 0, "1-40%": 0, "40%+": 0}
    for r in rows:
        lat0, lon0 = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
        obs = "양지" if str(r["볕"]).strip() == "1" else "그늘"
        sol = estimate_solar(lat0, lon0, when)
        az, el = sol.solar_azimuth_deg, sol.solar_elevation_deg
        mlat = 111_320.0
        mlon = 111_320.0 * math.cos(math.radians(lat0))

        hits = 0
        centre = "?"
        for k, (dx, dy) in enumerate(offsets):
            lat = lat0 + dy / mlat
            lon = lon0 + dx / mlon
            blocked, _ = await geo.sun_blocked_outdoor(lat, lon, az, el)
            if k == 0:
                centre = "그늘" if blocked else "양지"
            if blocked:
                hits += 1
        frac = hits / len(offsets)
        key = "0%" if hits == 0 else ("1-40%" if frac < 0.4 else "40%+")
        buckets[key] += 1
        print(f"  {r.get('지점명','')[:14]:<16}{obs:<6}{centre:<6}"
              f"{frac*100:8.0f}%   {el:5.1f}°")

    n = len(rows)
    print(f"\n  주변에 그늘이 전혀 없음   {buckets['0%']:3d}  "
          f"({100*buckets['0%']/n:4.0f} %)  -> 건물 데이터·기하 문제")
    print(f"  그늘이 있으나 좁음        {buckets['1-40%']:3d}  "
          f"({100*buckets['1-40%']/n:4.0f} %)  -> 위치 정확도 문제")
    print(f"  넓게 그늘인데 놓침        {buckets['40%+']:3d}  "
          f"({100*buckets['40%+']/n:4.0f} %)  -> 판정 로직 문제")


if __name__ == "__main__":
    asyncio.run(main())
