"""
완전 통합 라이브 파이프라인 — Street View 공간분석 + 실날씨 + MRT/UTCI.

좌표 하나(--lat/--lon)로 공간도 날씨도 전부 실데이터로 체감기후를 산출한다.

  ① Street View 5방향 수집 → SegFormer 세그멘테이션 → view별 SVF·BVI·GVI (실측+ML추론)
       (e2e_vpti_pipeline.py 의 stage1·stage2 로직 재사용)
  ② KMA 초단기실황 → 기온·습도·풍속·풍향 (실측)
     KMA 초단기예보 → 운량(SKY) — 실황엔 SKY가 없으므로 now에 가장 가까운 예보 사용
  ③ pvlib 일사 추정 → MRT(VDI 3787) → UTCI/PET (vpti_core.compute_vpti_thermal)

가산형 데모(demo_vpti_live.py)와 달리 공간 입력이 대표 프로파일이 아니라 실제
Street View 세그멘테이션 결과다. 각 단계 중간값을 모두 출력하고, 마지막에
"어느 값이 실측이고 어느 값이 추정인지" 출처 표를 찍는다.

실행 (CWD 무관):
    set PYTHONIOENCODING=utf-8
    python scripts/live_vpti_pipeline.py
    python scripts/live_vpti_pipeline.py --lat 35.18901 --lon 129.10069 --index pet

필요 .env: GOOGLE_STREETVIEW_API_KEY(+SIGNING_SECRET), SEGFORMER_MODEL_NAME, KMA_API_KEY.
도로축 추출기는 미구현이라 ROAD_AXIS_DEG/HEADING_DEG 가정값 사용(e2e 와 동일).
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
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))  # 형제 스크립트 import 용
os.chdir(BACKEND_DIR)  # pydantic-settings env_file=".env" 가 CWD 기준

# stage1(Street View)·stage2(SegFormer)·재질집계 로직 재사용
import e2e_vpti_pipeline as e2e  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.kma import KST, KMAClient, KMAObservation  # noqa: E402
from app.services.road_axis import RoadAxisResult, get_road_axis  # noqa: E402

import vpti_core  # noqa: E402

DEFAULT_LAT = 35.18901
DEFAULT_LON = 129.10069

# 도로축 OSM/GPS 추출 실패 시 폴백 가정값 (0=남북 도로). 보행 heading=북.
ASSUMED_ROAD_AXIS_DEG = e2e.ROAD_AXIS_DEG
HEADING_DEG = e2e.HEADING_DEG

# KMAForecast.sky_condition(문자열) → KMA SKY 코드(1/3/4) 역매핑.
SKY_STRING_TO_CODE = {"맑음": 1, "구름많음": 3, "흐림": 4}


def _banner(num, title: str) -> None:
    print(f"\n{'=' * 70}\n[단계 {num}] {title}\n{'=' * 70}")


# ============================================================================
# ② KMA — 실황(기온/습도/풍속) + 예보(SKY)
# ============================================================================
async def stage_kma_live(lat: float, lon: float, now: datetime):
    """실황 관측 + 초단기예보 SKY 동시 조회.

    returns: (obs, sky_code, sky_str, fcst_time)
    """
    _banner(3, "KMA — 초단기실황(기온/습도/풍속) + 초단기예보(SKY 운량)")
    settings = get_settings()
    key = settings.kma_api_key
    if not key or key.startswith("your_"):
        raise e2e.StageError("KMA_API_KEY 가 .env에 설정되지 않았습니다.")
    print(f"BASE URL       : {settings.kma_base_url}")

    client = KMAClient(api_key=key, base_url=settings.kma_base_url)
    try:
        obs = await client.get_current_observation(lat, lon)
        forecasts = await client.get_ultra_short_forecast(lat, lon)
    except Exception as e:  # noqa: BLE001
        raise e2e.StageError(f"KMA 호출 실패: {type(e).__name__}: {e}") from e
    finally:
        await client.close()

    print("\n  [초단기실황 getUltraSrtNcst] — 실측")
    print(f"  관측 기준시각  : {obs.observed_at:%Y-%m-%d %H:%M} KST")
    print(f"  기온           : {obs.temperature_c:.1f} °C   (T1H)")
    print(f"  습도           : {obs.humidity_pct:.0f} %     (REH)")
    print(f"  풍속/풍향      : {obs.wind_speed_ms:.1f} m/s / {obs.wind_direction_deg:.0f}°  (WSD/VEC)")
    print(f"  강수           : {obs.precipitation_mm:.1f} mm  (RN1)")

    sky_fcsts = [f for f in forecasts if f.sky_condition]
    if not sky_fcsts:
        print("\n  [초단기예보 getUltraSrtFcst] — SKY 없음 → 청천(운량 0) 가정")
        return obs, None, None, None
    nearest = min(sky_fcsts, key=lambda f: abs((f.forecast_for - now).total_seconds()))
    sky_code = SKY_STRING_TO_CODE.get(nearest.sky_condition)
    print("\n  [초단기예보 getUltraSrtFcst] — 운량(SKY)")
    print(f"  예보 시점      : {nearest.forecast_for:%Y-%m-%d %H:%M} KST (now에 최근접)")
    print(f"  하늘상태(SKY)  : {nearest.sky_condition} → 코드 {sky_code}")
    return obs, sky_code, nearest.sky_condition, nearest.forecast_for


# ============================================================================
# ③ vpti_core — 일사 → MRT → UTCI/PET (전부 실측 입력)
# ============================================================================
def stage_thermal(
    lat: float,
    lon: float,
    when: datetime,
    views_5,
    seg_by_dir,
    obs: KMAObservation,
    sky_code,
    road: RoadAxisResult,
    config: vpti_core.VPTICoreConfig,
) -> vpti_core.ThermalVPTIResult:
    _banner(5, "vpti_core — 일사 추정 → MRT → UTCI/PET")

    materials = e2e._aggregate_materials(seg_by_dir)
    print("재질 점유율 (5뷰 평균, SegFormer 추론):")
    for m in materials:
        print(f"  - {m.material:11s}: {m.fraction:.4f}")

    weather = vpti_core.WeatherContext(
        temperature_c=obs.temperature_c,
        wind_speed_ms=obs.wind_speed_ms,
        wind_direction_deg=obs.wind_direction_deg,
        humidity_pct=obs.humidity_pct,
    )
    print(f"\n도로축 {road.road_axis_deg:.1f}° (source={road.source}), 보행 heading={HEADING_DEG}°")

    try:
        result = vpti_core.compute_vpti_thermal(
            views_5=views_5,
            materials=materials,
            weather=weather,
            road_axis_deg=road.road_axis_deg,
            lat=lat,
            lon=lon,
            when=when,
            sky_code=sky_code,
            heading_deg=HEADING_DEG,
            config=config,
        )
    except Exception as e:  # noqa: BLE001
        raise e2e.StageError(f"vpti_core 산출 실패: {type(e).__name__}: {e}") from e

    d = result.as_dict()
    s, m, c = d["solar"], d["mrt"], d["comfort"]

    print("\n  ── 공간 지수 (Street View+SegFormer 실측) ──")
    print(f"  VSI            : {d['vsi']['vsi']:.4f}  "
          f"(SVF={d['vsi']['svf']:.3f}, GVI={d['vsi']['gvi']:.3f}, BVI={d['vsi']['bvi']:.3f})")
    p = d["pwi"]
    src_tag = {"osm": "실측(OSM)", "gps": "추정(GPS)", "assumed": "가정"}[road.source]
    print(f"  도로축/풍향 Δθ : 도로축 {road.road_axis_deg:.1f}°[{src_tag}] vs 풍향 "
          f"{obs.wind_direction_deg:.0f}° → Δθ={p['delta_theta_deg']:.1f}°  "
          f"(channeling C_ch={p['c_channel']:.3f})")
    print(f"  PWI            : {p['pwi']:.3f}  →  보행자 풍속 "
          f"{obs.wind_speed_ms:.1f} → {d['pedestrian_wind_ms']:.2f} m/s")

    print(f"\n  ── ① 일사 추정 (pvlib + 운량 {s['cloud_fraction']:.2f}) [추정] ──")
    print(f"  태양 고도/방위 : {s['solar_elevation_deg']:.1f}° / {s['solar_azimuth_deg']:.1f}°  (주간={s['is_daytime']})")
    print(f"  청천 GHI       : {s['ghi_clearsky']:.0f} W/m²")
    print(f"  추정 GHI/DNI/DHI: {s['ghi']:.0f} / {s['dni']:.0f} / {s['dhi']:.0f} W/m²")

    print("\n  ── ② MRT (VDI 3787 6방향 복사속) [추정] ──")
    print(f"  지면 알베도/ε  : {m['ground_albedo']:.3f} / {m['ground_emissivity']:.3f}  (재질→문헌 열물성)")
    print(f"  지표면온도     : {m['ground_temp_c']:.1f} °C   ε_sky={m['sky_emissivity']:.3f}")
    sw, lw = m["shortwave"], m["longwave"]
    print(f"  단파 [W/m²]    : 직달 {sw['direct']:.0f} + 산란 {sw['diffuse']:.0f} + 반사 {sw['reflected']:.0f}")
    print(f"  장파 [W/m²]    : 천공 {lw['sky']:.0f} + 지면 {lw['surface']:.0f}")
    print(f"  ▶ MRT (Tmrt)   : {m['tmrt']:.1f} °C")

    ci = c["inputs"]
    print(f"\n  ── ③ 체감지수 ({c['index'].upper()}, pythermalcomfort) ──")
    print(f"  입력(Tdb,Tr,v,RH): {ci['tdb']:.1f} °C, {ci['tr']:.1f} °C, {ci['v']:.2f} m/s, {ci['rh']:.0f} %")
    print(f"  ▶ {c['index'].upper():<14} : {c['value']:.1f} °C   ({c['stress_category']})")

    print(f"\n  ▶▶ VPTI (= {d['comfort_index'].upper()}) : {d['vpti']:.1f} °C   위험도={d['risk_level']}")
    return result


def print_provenance(result: vpti_core.ThermalVPTIResult, sky_str, road: RoadAxisResult) -> None:
    """값별 출처 표 — 실측 / ML추론 / 추정 / 가정 구분."""
    d = result.as_dict()
    s, m, c = d["solar"], d["mrt"], d["comfort"]
    v = d["vsi"]
    p = d["pwi"]
    road_src = {
        "osm": f"실측/DB(OSM {road.osm_name or road.osm_highway}, {road.distance_m:.0f}m)",
        "gps": "추정(GPS 이동방향)",
        "assumed": "가정(OSM·GPS 실패)",
    }[road.source]
    rows = [
        ("view별 SVF/BVI/GVI", f"SVF {v['svf']:.3f} / BVI {v['bvi']:.3f} / GVI {v['gvi']:.3f}",
         "실측(Street View 영상) + ML추론(SegFormer)"),
        ("지면 알베도/방사율", f"{m['ground_albedo']:.3f} / {m['ground_emissivity']:.3f}",
         "ML추론 재질 → 문헌 열물성(ASHRAE/Oke)"),
        ("기온", f"{c['inputs']['tdb']:.1f} °C", "실측(KMA 초단기실황 T1H)"),
        ("습도", f"{c['inputs']['rh']:.0f} %", "실측(KMA 초단기실황 REH)"),
        ("기준 풍속/풍향", f"{result.pwi.as_dict()['pedestrian_wind_speed_ms']:.2f} m/s(보행자)",
         "실측(KMA WSD/VEC) → PWI 변환"),
        ("운량(SKY)", f"{sky_str} → CF {s['cloud_fraction']:.2f}",
         "실측/예보(KMA 초단기예보 SKY)"),
        ("도로축", f"{road.road_axis_deg:.1f}°", road_src),
        ("풍향과 Δθ", f"{p['delta_theta_deg']:.1f}° (C_ch {p['c_channel']:.3f})",
         "계산(도로축 vs 풍향, 수학식 2)"),
        ("태양 위치", f"고도 {s['solar_elevation_deg']:.1f}°", "계산(pvlib NREL SPA, 결정론)"),
        ("일사 GHI/DNI/DHI", f"{s['ghi']:.0f}/{s['dni']:.0f}/{s['dhi']:.0f} W/m²",
         "추정(pvlib 청천 + Kasten-Czeplak 운량감쇠)"),
        ("지표면온도 Tsurf", f"{m['ground_temp_c']:.1f} °C",
         "추정(⚠️ ΔTsurf 단순화, UNCONFIRMED)"),
        ("MRT (Tmrt)", f"{m['tmrt']:.1f} °C", "추정(VDI 3787 6방향 복사모델)"),
        (f"{c['index'].upper()} / VPTI", f"{d['vpti']:.1f} °C",
         "추정(표준 라이브러리, 입력 일부 실측)"),
    ]
    print(f"\n{'=' * 70}\n■ 값 출처 (실측 / ML추론 / 추정 / 가정)\n{'=' * 70}")
    print(f"  {'항목':<20} {'값':<28} 출처")
    print(f"  {'-' * 20} {'-' * 28} {'-' * 16}")
    for name, val, src in rows:
        print(f"  {name:<20} {val:<28} {src}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="완전 통합 라이브 VPTI 파이프라인")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT)
    parser.add_argument("--lon", type=float, default=DEFAULT_LON)
    parser.add_argument("--index", choices=["utci", "pet"], default="utci",
                        help="체감지수 (default: utci)")
    args = parser.parse_args()

    config = vpti_core.VPTICoreConfig()
    if args.index == "pet":
        config = replace(config, comfort=replace(config.comfort, index="pet"))

    now = datetime.now(KST)
    print(f"ClimaX 완전 통합 라이브 파이프라인 — 좌표 ({args.lat}, {args.lon})")
    print(f"호출 시각(now): {now:%Y-%m-%d %H:%M:%S} KST   체감지수: {args.index.upper()}")

    try:
        # 단계 1·2: Street View + SegFormer (e2e 재사용, 자체 배너 출력)
        fetch = await e2e.stage1_street_view(args.lat, args.lon)
        views_5, seg_by_dir = e2e.stage2_segmentation(fetch)

        # 단계 3: KMA 실황 + SKY
        obs, sky_code, sky_str, _ = await stage_kma_live(args.lat, args.lon, now)
        if sky_code is None:
            sky_str = "맑음(가정)"

        # 단계 4: 도로축 추출 (OSM → GPS → 가정)
        _banner(4, "도로축 추출 — OSM(Overpass) → GPS → 가정")
        road = await get_road_axis(args.lat, args.lon, assumed_deg=ASSUMED_ROAD_AXIS_DEG)
        print(f"  도로축         : {road.road_axis_deg:.1f}°  (source={road.source})")
        if road.source == "osm":
            print(f"  최근접 도로    : {road.osm_name or '(이름없음)'} "
                  f"[{road.osm_highway}] way={road.osm_way_id}, {road.distance_m:.1f}m")
        else:
            print(f"  note           : {road.note}")

        # 단계 5: 일사 → MRT → UTCI/PET
        result = stage_thermal(
            args.lat, args.lon, now, views_5, seg_by_dir, obs, sky_code, road, config
        )
    except e2e.StageError as e:
        print(f"\n❌ 파이프라인 중단: {e}", file=sys.stderr)
        return 1

    print_provenance(result, sky_str, road)
    print(f"\n{'=' * 70}\n✅ 완전 통합 라이브 파이프라인 성공\n{'=' * 70}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
