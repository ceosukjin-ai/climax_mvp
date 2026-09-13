#!/usr/bin/env python3
"""가로수가 실제로 계산에 반영되는가 — 나무 끔/켬 PET 비교 (2026-09-12).

왜 이 시험인가:
  80지점 소급실행은 실측자가 적은 `볕`(0/1) 을 정답으로 **넣어 준다**. 그늘 여부가 이미 주어지므로
  가로수를 아무리 적재해도 그 결과는 변하지 않는다. 앱은 반대다 — 엔진이 그늘을 스스로 판정한다.
  그러므로 "가로수가 계산에 들어가는가"는 **같은 좌표·같은 시각에서 나무만 끄고 켜서** 봐야 한다.

방법:
  · 부산의 실제 몸씨 측정 좌표 중 반경 20m 안에 나무가 있는 곳을 표본으로 쓴다(실제 보행 지점).
  · 한여름 맑은 오후 조건을 고정한다 — 기상을 고정해야 차이가 나무에서만 온다.
  · routes.py 와 동일한 규약으로 두 번 계산한다.
        나무 끔 : direct_shade = 0.0 if blocked else 1.0
        나무 켬 : direct_shade = 0.0 if blocked else (1 - tree_f)
  · GVI 는 0 으로 고정한다 — 위성 녹지율까지 섞이면 나무 효과가 분리되지 않는다.

  docker exec climax-api python3 /tmp/tree_effect.py [표본수]
"""
from __future__ import annotations
import asyncio, statistics, sys
from datetime import datetime, timezone
sys.path.insert(0, "/app"); sys.path.insert(0, "/repo/backend")

from vpti_core import DEFAULT_CONFIG
from vpti_core.vsi import ViewSegmentation
from vpti_core.smti import MaterialFraction
from vpti_core.vpti import WeatherContext, compute_vpti_thermal
from vpti_core.comfort import compute_pet
from vpti_core.solar import estimate_solar
from app.services.geo import svf_geometric, sun_blocked_outdoor, tree_shade_factor

MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]
TA, RH, V, CF = 33.0, 60.0, 1.0, 0.1        # 한여름 맑은 날 고정
HOURS_UTC = [1, 4, 7]                        # 10시 · 13시 · 16시 (KST)
DAY = (2026, 8, 20)

SAMPLE_SQL = """
SELECT m.lat, m.lon, COUNT(*) AS n
  FROM (SELECT DISTINCT round(lat::numeric,5) AS lat, round(lon::numeric,5) AS lon
          FROM measurement
         WHERE indoor = FALSE AND lat BETWEEN 35.0 AND 35.4 AND lon BETWEEN 128.8 AND 129.3
         LIMIT 2000) m
  JOIN tree_point t
    -- geography 캐스팅은 GIST 인덱스를 무력화한다(266만행 전수스캔). 도(度) 경계상자로 거른다.
    ON t.geom && ST_MakeEnvelope(m.lon::float8 - 0.00024, m.lat::float8 - 0.00020,
                                 m.lon::float8 + 0.00024, m.lat::float8 + 0.00020, 4326)
 GROUP BY m.lat, m.lon
HAVING COUNT(*) >= 3
 ORDER BY n DESC
 LIMIT $1
"""

def views(svf, gvi=0.0):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi, building_ratio=b)
           for d in ("front", "back", "left", "right")]
    return vs


def run(lat, lon, when, svf, shade):
    wc = WeatherContext(temperature_c=TA, humidity_pct=RH, wind_speed_ms=V, wind_direction_deg=0.0)
    r = compute_vpti_thermal(views_5=views(svf), materials=MATS, weather=wc,
                             road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                             direct_shade=shade, wind_is_pedestrian=True, cloud_fraction=CF)
    pet = compute_pet(tdb=TA, tr=float(r.mrt.tmrt), v=r.pedestrian_wind_ms, rh=RH,
                      season=r.season, config=DEFAULT_CONFIG.comfort)
    return float(r.mrt.tmrt), float(pet.value)


async def main():
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    from app.services.skyline import _get_pool
    pool = await _get_pool()
    async with pool.acquire() as c:
        pts = await c.fetch(SAMPLE_SQL, want)
    print(f"표본 {len(pts)}지점 (부산 실측좌표 중 약20m 내 나무 3그루 이상)", flush=True)
    if not pts:
        print("표본 없음 — measurement 좌표 근처에 가로수가 없다.")
        return

    deltas, tfs, rows = [], [], []
    blocked_n = 0
    for _i, p in enumerate(pts, 1):
        if _i % 5 == 0:
            print(f"  {_i}/{len(pts)}", flush=True)
        lat, lon = float(p["lat"]), float(p["lon"])
        s = await svf_geometric(lat, lon)
        svf = float(s["svf"]) if s.get("svf") is not None else 1.0
        for h in HOURS_UTC:
            when = datetime(*DAY, h, 0, tzinfo=timezone.utc)
            sol = estimate_solar(lat, lon, when, config=DEFAULT_CONFIG.solar)
            blocked, _ = await sun_blocked_outdoor(lat, lon, sol.solar_azimuth_deg,
                                                   sol.solar_elevation_deg)
            if blocked:
                blocked_n += 1
                continue
            tf = await tree_shade_factor(lat, lon, sol.solar_azimuth_deg, sol.solar_elevation_deg)
            t0, p0 = run(lat, lon, when, svf, 1.0)          # 나무 끔
            t1, p1 = run(lat, lon, when, svf, 1.0 - tf)     # 나무 켬
            tfs.append(tf)
            if tf > 0:
                deltas.append(p1 - p0)
                rows.append((lat, lon, h + 9, tf, t0, t1, p0, p1))

    n_eval = len(tfs)
    n_hit = sum(1 for t in tfs if t > 0)
    print(f"평가 {n_eval}건 (건물이 이미 막은 {blocked_n}건 제외)")
    print(f"나무가 태양을 가린 경우 {n_hit}건 ({n_hit/max(n_eval,1)*100:.0f}%)\n")
    if not deltas:
        print("ΔPET 없음 — 나무가 태양 방향에 한 번도 걸리지 않았다.")
        print("가능한 원인: 수고 과소, 탐색반경 30m 밖, 또는 실제로 그런 배치다.")
        return
    deltas.sort()
    print(f"ΔPET (나무 켬 − 끔)   평균 {statistics.mean(deltas):+.2f}℃   "
          f"중앙값 {statistics.median(deltas):+.2f}℃   "
          f"최소 {deltas[0]:+.2f}   최대 {deltas[-1]:+.2f}")
    print(f"차광률 tree_f        평균 {statistics.mean([t for t in tfs if t>0]):.2f}\n")
    rows.sort(key=lambda r: r[7] - r[6])
    print("효과 큰 상위 10건 (좌표, 시각KST, 차광률, Tmrt 끔→켬, PET 끔→켬):")
    for la, lo, hh, tf, t0, t1, p0, p1 in rows[:10]:
        print(f"   {la:.5f},{lo:.5f}  {hh:02d}시  tf={tf:.2f}  "
              f"Tmrt {t0:5.1f}→{t1:5.1f}   PET {p0:5.1f}→{p1:5.1f}  ({p1-p0:+.1f})")
    print()
    print("판정: ΔPET 중앙값 −3 ~ −8℃ 면 타당. 0 이면 반영 안 됨. −15℃ 넘으면 수고·차광률 과대.")


if __name__ == "__main__":
    asyncio.run(main())
