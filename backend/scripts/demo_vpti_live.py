"""
부산 실시간 VPTI 데모 — 진짜 KMA 데이터로 일사 → MRT → UTCI/PET.

테스트용 가짜 날씨가 아니라 호출 시점(now)의 실제 기상을 받아 돌린다.
  · 기온·습도·풍속·풍향 : KMA 초단기실황(getUltraSrtNcst)
  · 운량(SKY)           : KMA 초단기예보(getUltraSrtFcst) — 실황엔 SKY가 없으므로
                          now에 가장 가까운 예보 시점의 하늘상태를 운량으로 사용
  · 일사(GHI/DNI/DHI)   : 좌표·now·운량으로 pvlib + Kasten-Czeplak 추정 (vpti_core.solar)
  · MRT, UTCI/PET       : vpti_core.mrt / comfort

⚠️ 공간 입력(VSI·SMTI: SVF/GVI/BVI·재질 점유율)은 라이브 Street View 세그멘테이션이
   이 스크립트에 연결돼 있지 않아 부산 가로 대표 프로파일을 사용한다. 날씨·운량·일사만
   실데이터다. (전체 라이브 파이프라인은 orchestrator 경유 — scripts/e2e_vpti_pipeline.py)

실행:
    set PYTHONIOENCODING=utf-8
    python scripts/demo_vpti_live.py
    python scripts/demo_vpti_live.py --index pet      # PET로
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)  # pydantic-settings env_file=".env" 가 CWD 기준

from app.config import get_settings  # noqa: E402
from app.services.kma import KST, KMAClient  # noqa: E402
from vpti_core import (  # noqa: E402
    MaterialFraction,
    VPTICoreConfig,
    ViewSegmentation,
    WeatherContext,
    compute_vpti_thermal,
)

BUSAN_LAT = 35.18901
BUSAN_LON = 129.10069
ROAD_AXIS_DEG = 30.0
HEADING_DEG = 30.0

# KMAForecast.sky_condition(문자열) → KMA SKY 코드(1/3/4) 역매핑.
# (KMAClient.SKY_CODE = {"1":"맑음","3":"구름많음","4":"흐림"} 의 역)
SKY_STRING_TO_CODE = {"맑음": 1, "구름많음": 3, "흐림": 4}


def _busan_street_profile() -> tuple[list[ViewSegmentation], list[MaterialFraction]]:
    """부산 도심 가로 대표 공간 프로파일 (⚠️ 라이브 세그멘테이션 아님)."""
    views = [
        ViewSegmentation("up", sky_ratio=0.45, vegetation_ratio=0.05, building_ratio=0.50),
        ViewSegmentation("front", sky_ratio=0.15, vegetation_ratio=0.12, building_ratio=0.65),
        ViewSegmentation("back", sky_ratio=0.18, vegetation_ratio=0.10, building_ratio=0.62),
        ViewSegmentation("left", sky_ratio=0.10, vegetation_ratio=0.18, building_ratio=0.68),
        ViewSegmentation("right", sky_ratio=0.12, vegetation_ratio=0.15, building_ratio=0.70),
    ]
    materials = [
        MaterialFraction("asphalt", 0.55),
        MaterialFraction("concrete", 0.30),
        MaterialFraction("vegetation", 0.15),
    ]
    return views, materials


async def _fetch_weather_and_sky(client: KMAClient, lat: float, lon: float, now: datetime):
    """실황(기온/습도/풍속) + 예보(SKY) 동시 조회. (obs, sky_code, sky_str, fcst_time) 반환."""
    obs = await client.get_current_observation(lat, lon)
    forecasts = await client.get_ultra_short_forecast(lat, lon)

    sky_fcsts = [f for f in forecasts if f.sky_condition]
    if not sky_fcsts:
        return obs, None, None, None
    nearest = min(sky_fcsts, key=lambda f: abs((f.forecast_for - now).total_seconds()))
    sky_code = SKY_STRING_TO_CODE.get(nearest.sky_condition)
    return obs, sky_code, nearest.sky_condition, nearest.forecast_for


def _print_result(result, obs, sky_str, fcst_time, now) -> None:
    d = result.as_dict()
    print(f"\n{'=' * 66}")
    print(f"■ 부산 실시간 VPTI ({BUSAN_LAT}, {BUSAN_LON})")
    print(f"  호출 시각(now)     : {now:%Y-%m-%d %H:%M:%S} KST   계절={d['season']}")
    print(f"{'=' * 66}")

    print("\n  ── KMA 실측 (초단기실황 getUltraSrtNcst) ──")
    print(f"  관측 기준시각      : {obs.observed_at:%Y-%m-%d %H:%M} KST")
    print(f"  기온               : {obs.temperature_c:.1f} °C  (T1H)")
    print(f"  습도               : {obs.humidity_pct:.0f} %    (REH)")
    print(f"  풍속/풍향          : {obs.wind_speed_ms:.1f} m/s / {obs.wind_direction_deg:.0f}°  (WSD/VEC)")
    print(f"  강수               : {obs.precipitation_mm:.1f} mm (RN1)")

    print("\n  ── KMA 운량 (초단기예보 getUltraSrtFcst, SKY) ──")
    print(f"  예보 시점          : {fcst_time:%Y-%m-%d %H:%M} KST")
    s = d["solar"]
    print(f"  하늘상태(SKY)      : {sky_str} → 코드 {s['sky_code']} → 운량 {s['cloud_fraction']:.2f}")

    print("\n  ── ① 일사 추정 (pvlib + 운량 감쇠) ──")
    print(f"  태양 고도/방위     : {s['solar_elevation_deg']:.1f}° / {s['solar_azimuth_deg']:.1f}°  (주간={s['is_daytime']})")
    print(f"  청천 GHI           : {s['ghi_clearsky']:.0f} W/m²")
    print(f"  추정 GHI/DNI/DHI   : {s['ghi']:.0f} / {s['dni']:.0f} / {s['dhi']:.0f} W/m²")

    print("\n  ── 공간/재질 지수 (⚠️ 대표 프로파일, 라이브 아님) ──")
    print(f"  VSI                : {d['vsi']['vsi']:.4f}  "
          f"(SVF={d['vsi']['svf']:.2f}, GVI={d['vsi']['gvi']:.2f}, BVI={d['vsi']['bvi']:.2f})")
    p = d["pwi"]
    print(f"  PWI                : {p['pwi']:.3f}  →  보행자 풍속 "
          f"{obs.wind_speed_ms:.1f} → {d['pedestrian_wind_ms']:.2f} m/s")

    m = d["mrt"]
    print("\n  ── ② MRT (VDI 3787 6방향 복사속) ──")
    print(f"  지면 알베도/방사율 : {m['ground_albedo']:.3f} / {m['ground_emissivity']:.3f}  (SMTI 재질 도출)")
    print(f"  추정 지표면온도    : {m['ground_temp_c']:.1f} °C   천공 방사율 ε_sky={m['sky_emissivity']:.3f}")
    sw, lw = m["shortwave"], m["longwave"]
    print(f"  단파 흡수 [W/m²]   : 직달 {sw['direct']:.0f} + 산란 {sw['diffuse']:.0f} + 반사 {sw['reflected']:.0f}  (fp={sw['fp']:.3f})")
    print(f"  장파 흡수 [W/m²]   : 천공 {lw['sky']:.0f} + 지면 {lw['surface']:.0f}")
    print(f"  평균복사속 Sstr    : {m['sstr']:.0f} W/m²")
    print(f"  ▶ MRT (Tmrt)       : {m['tmrt']:.1f} °C")

    c = d["comfort"]
    ci = c["inputs"]
    print(f"\n  ── ③ 체감지수 ({c['index'].upper()}, pythermalcomfort) ──")
    print(f"  입력 (Tdb,Tr,v,RH) : {ci['tdb']:.1f} °C, {ci['tr']:.1f} °C, {ci['v']:.2f} m/s, {ci['rh']:.0f} %")
    print(f"  ▶ {c['index'].upper():<16} : {c['value']:.1f} °C   ({c['stress_category']})")

    print(f"\n  ▶▶ VPTI (= {d['comfort_index'].upper()}) : {d['vpti']:.1f} °C   위험도={d['risk_level']}")
    print(f"{'=' * 66}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="부산 실시간 VPTI 데모 (KMA 실데이터)")
    parser.add_argument("--index", choices=["utci", "pet"], default="utci",
                        help="체감지수 선택 (default: utci)")
    args = parser.parse_args()

    s = get_settings()
    if not s.kma_api_key:
        print("[FAIL] KMA_API_KEY is empty in .env — 실데이터 조회 불가")
        return 1

    config = VPTICoreConfig()
    if args.index == "pet":
        config = replace(config, comfort=replace(config.comfort, index="pet"))

    now = datetime.now(KST)

    async with KMAClient(api_key=s.kma_api_key, base_url=s.kma_base_url) as client:
        try:
            obs, sky_code, sky_str, fcst_time = await _fetch_weather_and_sky(
                client, BUSAN_LAT, BUSAN_LON, now
            )
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] KMA 조회 실패: {type(e).__name__}: {e}")
            return 1

    if sky_code is None:
        print("[WARN] 초단기예보에서 SKY를 못 받음 → 청천(운량 0) 가정으로 진행")

    weather = WeatherContext(
        temperature_c=obs.temperature_c,
        wind_speed_ms=obs.wind_speed_ms,
        wind_direction_deg=obs.wind_direction_deg,
        humidity_pct=obs.humidity_pct,
    )
    views, materials = _busan_street_profile()

    result = compute_vpti_thermal(
        views_5=views,
        materials=materials,
        weather=weather,
        road_axis_deg=ROAD_AXIS_DEG,
        lat=BUSAN_LAT,
        lon=BUSAN_LON,
        when=now,
        sky_code=sky_code,
        heading_deg=HEADING_DEG,
        config=config,
    )
    _print_result(result, obs, sky_str, fcst_time, now)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
