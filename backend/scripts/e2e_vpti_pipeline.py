"""
단일 좌표 end-to-end 통합 테스트 — Street View → SegFormer → KMA → vpti_core.

실제 외부 API와 ML 추론을 모두 태워 vpti_core 엔진까지 한 번에 검증한다.

흐름:
    ① Google Street View Static API로 5방향(전·후·좌·우·천정) 이미지 수집·저장
       (.env: GOOGLE_STREETVIEW_API_KEY / GOOGLE_STREETVIEW_SIGNING_SECRET)
    ② SegFormer 세그멘테이션으로 view별 sky/veg/building 비율 → SVF·BVI·GVI
       (.env: SEGFORMER_MODEL_NAME, 최초 1회 HuggingFace 다운로드)
    ③ KMA 초단기실황으로 기온·습도·풍속·풍향, 초단기예보로 운량(SKY) 수신
       (.env: KMA_API_KEY / KMA_BASE_URL)
    ④ vpti_core 물리경로(일사 → MRT → UTCI/PET)로 VPTI 산출
       (VPTI ≡ UTCI. 가산형이 아니라 표준 체감지수로 통일됨)

실행 (CWD 무관 — 스크립트가 backend/를 sys.path에 추가하고 chdir):
    set PYTHONIOENCODING=utf-8
    python scripts/e2e_vpti_pipeline.py
    python scripts/e2e_vpti_pipeline.py --lat 35.18901 --lon 129.10069

각 단계의 중간 결과를 모두 출력하며, 어느 단계에서 막히면 그 지점에서
명확한 에러 메시지를 내고 종료 코드 1로 끝낸다(성공 시 0).

도로축(road axis): PWI 수학식 2의 도로축은 app.services.road_axis 추출기가
    OSM(Overpass) → GPS → 가정값 순으로 산출한다. ROAD_AXIS_DEG 는 OSM·GPS 가
    모두 실패할 때의 폴백 가정값일 뿐이다.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)  # pydantic-settings env_file=".env" 가 CWD 기준이라 필요

from app.config import get_settings  # noqa: E402
from app.ml.segformer import SegFormerService, SegmentationOutput  # noqa: E402
from app.services.kma import KST, KMAClient, KMAObservation  # noqa: E402
from app.services.road_axis import RoadAxisResult, get_road_axis  # noqa: E402
from app.services.street_view import (  # noqa: E402
    GoogleStreetViewClient,
    StreetViewError,
    StreetViewFetchResult,
)

import vpti_core  # noqa: E402

# KMAForecast.sky_condition(문자열) → KMA SKY 코드(1/3/4) 역매핑.
SKY_STRING_TO_CODE = {"맑음": 1, "구름많음": 3, "흐림": 4}

# 기본 좌표: 부산 (요청 좌표)
DEFAULT_LAT = 35.18901
DEFAULT_LON = 129.10069

# 이미지 저장 위치
OUTPUT_DIR = BACKEND_DIR / "scripts" / "_e2e_output"

# vpti_core ViewSegmentation 방향명 ↔ Street View 키 (동일하게 사용)
VIEW_DIRECTIONS = ("front", "back", "left", "right", "up")

# 도로축 OSM/GPS 추출 실패 시 폴백 가정값 (0.0 = 남북 도로).
ROAD_AXIS_DEG = 0.0
HEADING_DEG = 0.0  # 보행자 진행 방향 = Street View 'front'(북)


class StageError(Exception):
    """단계 실패를 명확히 표시하기 위한 예외."""


def _banner(num: int, title: str) -> None:
    print(f"\n{'=' * 70}\n[단계 {num}] {title}\n{'=' * 70}")


# ============================================================================
# ① Street View
# ============================================================================
async def stage1_street_view(lat: float, lon: float) -> StreetViewFetchResult:
    _banner(1, "Google Street View — 5방향 이미지 수집")
    settings = get_settings()
    key = settings.google_streetview_api_key
    if not key or key.startswith("your_"):
        raise StageError(
            "GOOGLE_STREETVIEW_API_KEY 가 .env에 설정되지 않았습니다. "
            "(ClimaX-StreetView 키를 .env의 GOOGLE_STREETVIEW_API_KEY에 넣으세요)"
        )
    print(f"좌표           : ({lat}, {lon})")
    print(f"API KEY 길이   : {len(key)}")

    client = GoogleStreetViewClient(
        api_key=key,
        signing_secret=settings.google_streetview_signing_secret,
    )
    try:
        meta = await client.get_pano_metadata(lat, lon)
        if meta.status != "OK":
            raise StageError(
                f"이 좌표 근처에 Street View 파노라마가 없습니다 (status={meta.status}). "
                "좌표를 도로변으로 옮기거나 radius를 늘려야 합니다."
            )
        print(f"panoId         : {meta.pano_id}")
        print(f"촬영일         : {meta.date}")
        result = await client.fetch_five_views(meta)
    except StreetViewError as e:
        raise StageError(f"Street View 호출 실패: {e}") from e
    finally:
        await client.close()

    # 저장
    out_dir = OUTPUT_DIR / f"{lat}_{lon}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n받은 이미지 개수: {len(result.images)} / 5")
    for direction in VIEW_DIRECTIONS:
        img = result.images.get(direction)
        if img is None:
            raise StageError(f"'{direction}' 방향 이미지가 응답에 없습니다.")
        path = out_dir / f"{direction}.jpg"
        path.write_bytes(img)
        print(f"  - {direction:5s}: {len(img):>7,} bytes  →  {path}")

    if len(result.images) != 5:
        raise StageError(f"5방향이 모두 수집되지 않았습니다 (got {len(result.images)}).")
    return result


# ============================================================================
# ② SegFormer
# ============================================================================
def stage2_segmentation(
    fetch: StreetViewFetchResult,
) -> tuple[list[vpti_core.ViewSegmentation], dict[str, SegmentationOutput]]:
    _banner(2, "SegFormer 세그멘테이션 — view별 SVF·BVI·GVI 추출")
    settings = get_settings()
    service = SegFormerService(
        model_name=settings.segformer_model_name,
        checkpoint_path=settings.segformer_checkpoint_path,
        device=settings.segformer_device,
    )
    print(f"모델 로딩 중... ({settings.segformer_model_name}) — 최초 실행 시 다운로드로 수십 초 소요")
    try:
        service.load()
    except Exception as e:  # noqa: BLE001 — 로딩 실패 원인을 그대로 노출
        raise StageError(
            f"SegFormer 로드 실패: {type(e).__name__}: {e}\n"
            "  torch/transformers 설치 여부와 네트워크(HuggingFace 다운로드)를 확인하세요."
        ) from e

    seg_by_dir: dict[str, SegmentationOutput] = {}
    views_5: list[vpti_core.ViewSegmentation] = []
    print(f"\n{'view':6s} {'sky(SVF)':>10s} {'veg(GVI)':>10s} {'bld(BVI)':>10s}")
    print("-" * 40)
    for direction in VIEW_DIRECTIONS:
        try:
            seg = service.segment(fetch.images[direction])
        except Exception as e:  # noqa: BLE001
            raise StageError(
                f"'{direction}' 추론 실패: {type(e).__name__}: {e}"
            ) from e
        seg_by_dir[direction] = seg
        views_5.append(
            vpti_core.ViewSegmentation(
                direction=direction,
                sky_ratio=seg.sky_ratio,
                vegetation_ratio=seg.vegetation_ratio,
                building_ratio=seg.building_ratio,
            )
        )
        print(
            f"{direction:6s} {seg.sky_ratio:>10.4f} "
            f"{seg.vegetation_ratio:>10.4f} {seg.building_ratio:>10.4f}"
        )

    # 집계 미리보기 (vpti_core가 내부적으로 동일 산출)
    comp = vpti_core.extract_components(views_5)
    print(
        f"\n집계 → SVF(상향)={comp.svf:.4f}, "
        f"GVI(수평평균)={comp.gvi:.4f}, BVI(수평평균)={comp.bvi:.4f}"
    )
    return views_5, seg_by_dir


def _aggregate_materials(
    seg_by_dir: dict[str, SegmentationOutput],
) -> list[vpti_core.MaterialFraction]:
    """5-view 재질 비율 평균 → SMTI용 MaterialFraction 리스트.

    각 view material_ratios(픽셀 비율 [0,1])를 view 수로 평균하여 [0,1] 유지.
    compute_smti가 Σ P_i = 1 로 정규화(수학식 6)하므로 합이 1 미만이어도 무방.
    """
    n = len(seg_by_dir)
    totals: dict[str, float] = {}
    for seg in seg_by_dir.values():
        for mat, ratio in seg.material_ratios.items():
            totals[mat] = totals.get(mat, 0.0) + ratio
    fractions = [(m, v / n) for m, v in totals.items() if v > 0.0]
    if not fractions:
        # 표면 재질이 전혀 잡히지 않으면 unknown으로 폴백
        return [vpti_core.MaterialFraction("unknown", 1.0)]
    return [vpti_core.MaterialFraction(m, f) for m, f in fractions]


# ============================================================================
# ③ KMA
# ============================================================================
async def stage3_kma(lat: float, lon: float, now: datetime):
    """초단기실황(기온/습도/풍속/풍향) + 초단기예보(SKY 운량) 동시 수신.

    실황엔 SKY가 없으므로 now에 가장 가까운 예보 시점의 하늘상태를 운량으로 쓴다.

    returns: (obs, sky_code, sky_str)
    """
    _banner(3, "KMA — 초단기실황(기온/습도/풍속) + 초단기예보(SKY 운량)")
    settings = get_settings()
    key = settings.kma_api_key
    if not key or key.startswith("your_"):
        raise StageError("KMA_API_KEY 가 .env에 설정되지 않았습니다.")
    print(f"BASE URL       : {settings.kma_base_url}")
    print(f"API KEY 길이   : {len(key)}")

    client = KMAClient(api_key=key, base_url=settings.kma_base_url)
    try:
        obs = await client.get_current_observation(lat, lon)
        forecasts = await client.get_ultra_short_forecast(lat, lon)
    except Exception as e:  # noqa: BLE001 — KMA 파싱/네트워크 오류 그대로 노출
        raise StageError(f"KMA 호출 실패: {type(e).__name__}: {e}") from e
    finally:
        await client.close()

    print("\n  [초단기실황] — 실측")
    print(f"  관측 시각      : {obs.observed_at.isoformat()}")
    print(f"  기온           : {obs.temperature_c:.1f} °C")
    print(f"  습도           : {obs.humidity_pct:.0f} %")
    print(f"  ▶ 기준 풍속    : {obs.wind_speed_ms:.1f} m/s")
    print(f"  ▶ 풍향         : {obs.wind_direction_deg:.0f}° (0=북, 90=동)")
    print(f"  강수량         : {obs.precipitation_mm:.1f} mm")

    sky_fcsts = [f for f in forecasts if f.sky_condition]
    if not sky_fcsts:
        print("\n  [초단기예보] — SKY 없음 → 청천(운량 0) 가정")
        return obs, None, None
    nearest = min(sky_fcsts, key=lambda f: abs((f.forecast_for - now).total_seconds()))
    sky_code = SKY_STRING_TO_CODE.get(nearest.sky_condition)
    print("\n  [초단기예보] — 운량(SKY)")
    print(f"  예보 시점      : {nearest.forecast_for:%Y-%m-%d %H:%M} KST (now 최근접)")
    print(f"  ▶ 하늘상태     : {nearest.sky_condition} → 코드 {sky_code}")
    return obs, sky_code, nearest.sky_condition


# ============================================================================
# ④ vpti_core — 물리경로: 일사 → MRT → UTCI/PET  (VPTI ≡ UTCI)
# ============================================================================
def stage4_vpti(
    lat: float,
    lon: float,
    when: datetime,
    views_5: list[vpti_core.ViewSegmentation],
    seg_by_dir: dict[str, SegmentationOutput],
    obs: KMAObservation,
    sky_code: int | None,
    road: RoadAxisResult,
) -> vpti_core.ThermalVPTIResult:
    _banner(5, "vpti_core — 일사 → MRT → UTCI/PET (VPTI ≡ UTCI)")

    materials = _aggregate_materials(seg_by_dir)
    print("재질 점유율(5뷰 평균, 정규화 전):")
    for m in materials:
        print(f"  - {m.material:11s}: {m.fraction:.4f}")

    print(f"\n도로축 {road.road_axis_deg:.1f}° (source={road.source}), 보행 heading={HEADING_DEG}°")

    weather = vpti_core.WeatherContext(
        temperature_c=obs.temperature_c,
        wind_speed_ms=obs.wind_speed_ms,
        wind_direction_deg=obs.wind_direction_deg,
        humidity_pct=obs.humidity_pct,
    )

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
        )
    except Exception as e:  # noqa: BLE001
        raise StageError(f"vpti_core 산출 실패: {type(e).__name__}: {e}") from e

    d = result.as_dict()
    s, m, c = d["solar"], d["mrt"], d["comfort"]
    print(f"\n{'-' * 50}\n■ 최종 산출 결과 (계절={d['season']})\n{'-' * 50}")
    print(f"  VSI   : {d['vsi']['vsi']:.4f}  "
          f"(SVF={d['vsi']['svf']:.3f}, GVI={d['vsi']['gvi']:.3f}, BVI={d['vsi']['bvi']:.3f})")
    p = d["pwi"]
    src_tag = {"osm": "실측(OSM)", "gps": "추정(GPS)", "assumed": "가정"}[road.source]
    print(f"  도로축 Δθ: 도로축 {road.road_axis_deg:.1f}°[{src_tag}] vs 풍향 "
          f"{obs.wind_direction_deg:.0f}° → Δθ={p['delta_theta_deg']:.1f}° (C_ch={p['c_channel']:.3f})")
    print(f"  PWI   : {p['pwi']:.3f}  u_ref={obs.wind_speed_ms:.1f} → u_p={d['pedestrian_wind_ms']:.2f} m/s")
    print(f"  ① 일사: GHI/DNI/DHI = {s['ghi']:.0f}/{s['dni']:.0f}/{s['dhi']:.0f} W/m²  "
          f"(태양고도 {s['solar_elevation_deg']:.1f}°, 운량 {s['cloud_fraction']:.2f})")
    print(f"  ② MRT : {m['tmrt']:.1f} °C  "
          f"(지표면 {m['ground_temp_c']:.1f} °C, α={m['ground_albedo']:.3f}, ε_sky={m['sky_emissivity']:.3f})")
    print(f"  ③ {c['index'].upper()} : {c['value']:.1f} °C  ({c['stress_category']})  "
          f"[Tdb {c['inputs']['tdb']:.1f}, Tr {c['inputs']['tr']:.1f}, v {c['inputs']['v']:.2f}, RH {c['inputs']['rh']:.0f}]")
    print(f"  ▶ VPTI (= {d['comfort_index'].upper()}): {d['vpti']:.1f} °C   위험도={d['risk_level']}")
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description="단일 좌표 end-to-end VPTI 파이프라인")
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT)
    parser.add_argument("--lon", type=float, default=DEFAULT_LON)
    args = parser.parse_args()

    now = datetime.now(KST)
    print(f"ClimaX end-to-end 통합 테스트 — 좌표 ({args.lat}, {args.lon})")
    print(f"호출 시각(now): {now:%Y-%m-%d %H:%M:%S} KST")

    try:
        fetch = await stage1_street_view(args.lat, args.lon)
        views_5, seg_by_dir = stage2_segmentation(fetch)
        obs, sky_code, _sky_str = await stage3_kma(args.lat, args.lon, now)
        _banner(4, "도로축 추출 — OSM(Overpass) → GPS → 가정")
        road = await get_road_axis(args.lat, args.lon, assumed_deg=ROAD_AXIS_DEG)
        print(f"도로축: {road.road_axis_deg:.1f}° (source={road.source})"
              + (f" — {road.osm_name or road.osm_highway}, {road.distance_m:.1f}m"
                 if road.source == "osm" else f" — {road.note}"))
        stage4_vpti(args.lat, args.lon, now, views_5, seg_by_dir, obs, sky_code, road)
    except StageError as e:
        print(f"\n❌ 파이프라인 중단: {e}", file=sys.stderr)
        return 1

    print(f"\n{'=' * 70}\n✅ end-to-end 파이프라인 전 단계 성공\n{'=' * 70}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
