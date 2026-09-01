#!/usr/bin/env python3
"""좌표 강수 판정 실동작 확인 — 실제 기상청 자료로 끝까지 돌려본다.

개발 샌드박스에서는 기상청으로 통신이 안 나가므로, **맥 터미널에서** 돌린다.

    cd ~/Desktop/climax_mvp/backend
    set -a; . ./.env; set +a
    .venv/bin/python scripts/rain_smoke.py                 # 연산동
    .venv/bin/python scripts/rain_smoke.py 35.1631 129.1636   # 해운대
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.aws_obs import AWSObsClient          # noqa: E402
from app.services.kma import ASOSClient, KMAClient     # noqa: E402
from app.services.rain import RainService, to_dict     # noqa: E402


DEFAULT_LAT, DEFAULT_LON = 35.1846, 129.0800      # 부산 연제구 연산동


def _coords() -> tuple[float, float]:
    """인자로 좌표 두 개를 받는다. 숫자가 아니면 기본 좌표를 쓴다.

    zsh 대화형 셸은 `#` 뒤를 주석으로 보지 않아서 설명글이 인자로 딸려 들어온다.
    그런 걸로 스크립트가 죽지 않게 한다.
    """
    nums: list[float] = []
    for a in sys.argv[1:]:
        try:
            nums.append(float(a))
        except ValueError:
            continue
    if len(nums) >= 2 and -90 <= nums[0] <= 90 and -180 <= nums[1] <= 180:
        return nums[0], nums[1]
    return DEFAULT_LAT, DEFAULT_LON


async def main() -> None:
    lat, lon = _coords()

    portal = os.environ.get("KMA_API_KEY", "")
    hub = os.environ.get("KMA_APIHUB_KEY", "")
    if not portal or not hub:
        print("KMA_API_KEY / KMA_APIHUB_KEY 가 없습니다. `set -a; . ./.env; set +a` 후 다시.")
        return

    kma = KMAClient(api_key=portal)
    asos = ASOSClient(auth_key=hub)
    aws = AWSObsClient(auth_key=hub)

    print("=" * 68)
    print("  AWS 실측 점검")
    print("=" * 68)

    got = await aws.ensure_stations()
    print(f"지점 좌표표      {len(aws.registry.stations):>5}곳  {'✅' if got else '❌'}")

    # 단계마다 시간을 찍는다 — 어디서 오래 걸리는지 보여야 한다
    import time as _t

    async def step(label, coro):
        t0 = _t.monotonic()
        print(f"  {label} ...", end="", flush=True)
        try:
            out = await coro
        except Exception as e:            # noqa: BLE001
            print(f" 실패 ({e.__class__.__name__}: {e})", flush=True)
            return {}
        print(f" {len(out)}곳  {_t.monotonic() - t0:.1f}초", flush=True)
        return out

    print("\n자료 받는 중 (전 지점 조회라 각 10~30초 걸릴 수 있습니다)")
    minute = await step("매분자료  ", aws.minute_all())
    hourly = await step("시간통계  ", aws.hourly_rain_all())
    vis = await step("시정/현천 ", aws.visibility_all())
    cloud = await step("운고운량  ", aws.cloud_all())
    radar = await step("레이더    ", aws.radar_all())
    aws.registry.flush()
    merged = aws.merge_parts(minute, hourly, vis, cloud, radar)
    print()

    have = lambda f: sum(1 for v in merged.values() if f(v))  # noqa: E731
    print(f"관측 지점         {len(merged):>5}곳")
    print(f"  강수량 RN        {have(lambda v: v.rn_mm is not None):>5}곳")
    print(f"  강수감지 RE      {have(lambda v: v.re_flag is not None):>5}곳")
    print(f"  RE_SUM(분)       {have(lambda v: v.re_min is not None):>5}곳")
    print(f"  현천 WW1         {have(lambda v: v.ww is not None):>5}곳")
    print(f"  운고 CH_LOW      {have(lambda v: v.cloud_base_m is not None):>5}곳")
    print(f"  이슬점 TD        {have(lambda v: v.td_c is not None):>5}곳")
    print(f"  레이더 값        {have(lambda v: v.radar_mmh is not None):>5}곳")
    print(f"  에코 고도        {have(lambda v: v.echo_height_m is not None):>5}곳")

    judged = [v for v in merged.values() if v.intensity is not None]
    rain = [v for v in merged.values() if v.intensity == "비"]
    drizzle = [v for v in merged.values() if v.intensity == "빗방울"]
    recent = [v for v in merged.values() if v.recent_rain]
    print(f"\n판정 가능         {len(judged):>5}곳 / 전체 {len(merged)}곳")
    print(f"지금 비           {len(rain):>5}곳")
    print(f"지금 빗방울       {len(drizzle):>5}곳")
    print(f"지난 1시간 비     {len(recent):>5}곳  (지금과 구분)")

    agree = [v for v in merged.values() if v.sky_vs_ground == "일치"]
    sky_only = [v for v in merged.values() if v.sky_vs_ground == "하늘만"]
    ground_only = [v for v in merged.values() if v.sky_vs_ground == "땅만"]
    print(f"\n하늘 × 땅 대조    일치 {len(agree)}곳 · "
          f"하늘만 {len(sky_only)}곳 · 땅만 {len(ground_only)}곳")
    for st in sky_only[:4]:
        h = f"{st.echo_height_m:.0f}m" if st.echo_height_m else "?"
        print(f"   [하늘만] {aws.registry.name(st.stn):<12} "
              f"레이더 {st.radar_mmh}mm/h · 에코고도 {h} · 습수 {st.dew_depression_c}")
    for st in ground_only[:4]:
        print(f"   [땅만]   {aws.registry.name(st.stn):<12} "
              f"{st.intensity} ({st.evidence}) · 레이더 {st.radar_mmh}mm/h")
    for st in (rain + drizzle)[:8]:
        print(f"   {aws.registry.name(st.stn):<12} {st.intensity:<4} {st.evidence:<5} "
              f"RN15={st.rn_mm} RN60={st.rn_hour_mm} RE_SUM={st.re_min} WW={st.ww}")

    # 부산 반경에 실제로 쓸 수 있는 지점이 몇 곳인지 — 이게 제일 중요하다
    from app.services.kma import haversine_km
    near = [
        (s_, haversine_km(lat, lon, *aws.registry.stations[s_.stn]))
        for s_ in merged.values() if s_.stn in aws.registry.stations
    ]
    for radius in (15, 30, 60, 120):
        inside = [(v, d) for v, d in near if d <= radius]
        ok = [v for v, _ in inside if v.raining is not None]
        ww = [v for v, _ in inside if v.ww is not None]
        print(f"반경 {radius:>3}km 안    지점 {len(inside):>3}곳 · "
              f"판정가능 {len(ok):>3}곳 · 현천 {len(ww):>3}곳")

    dep = [v.dew_depression_c for v in merged.values() if v.dew_depression_c is not None]
    if dep:
        print(f"\n습수(기온−이슬점) {len(dep)}곳 · 중앙값 "
              f"{sorted(dep)[len(dep)//2]:.1f}°C  (클수록 증발 가능성 ↑)")

    print("\n" + "=" * 68)
    print(f"  판정 결과 — ({lat}, {lon})")
    print("=" * 68)
    svc = RainService(kma=kma, asos=asos, aws=aws)
    r = await svc.rain_at(lat, lon)
    d = to_dict(r)
    for k in ("observation_source", "source", "confidence", "data_at", "data_age_min",
              "stale", "raining_now", "onset_at", "clearing_at", "umbrella_window",
              "forecast_vs_observation", "eta_min_range"):
        print(f"   {k:<24} {d.get(k)}")
    if d.get("nearest_rain"):
        print(f"   가장 가까운 비           {d['nearest_rain']}")
    print(f"\n   ▶ {d['advice']}")

    await aws.close()
    await asos.close()
    await kma.close()


if __name__ == "__main__":
    asyncio.run(main())
