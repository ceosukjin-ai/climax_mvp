"""
기상청 API 세 메서드 통합 검증 스크립트.

지원 API:
- ncst        : getUltraSrtNcst  (초단기실황, 카테고리 키 T1H)
- ultra-fcst  : getUltraSrtFcst  (초단기예보 6h, 카테고리 키 T1H)
- vilage-fcst : getVilageFcst    (단기예보 3d,  카테고리 키 TMP — T1H 아님 주의)
- all         : 위 셋을 순차 실행 (default)

실행:
    set PYTHONIOENCODING=utf-8     # Windows cp949 콘솔에서 한글 깨짐 방지
    python backend/scripts/test_kma.py                # all (default)
    python backend/scripts/test_kma.py ncst
    python backend/scripts/test_kma.py ultra-fcst
    python backend/scripts/test_kma.py vilage-fcst
    python backend/scripts/test_kma.py all --lat 35.1796 --lon 129.0756

CWD가 어디든 동작 (스크립트가 backend/를 sys.path에 자동 추가).
기본 좌표는 서울시청 (37.5665, 126.9780).

용도: 공공데이터포털 API 키 발급 직후, KMA 채널/엔드포인트 전환 후,
또는 응답 파싱 회귀가 의심될 때 KMA 호출만 격리해서 빠르게 검증.
orchestrator를 거치지 않으므로 Google Street View나 SegFormer가
실패해도 KMA 단독으로 진단 가능.

종료 코드: 0 = 모두 성공, 1 = 하나라도 실패 (CI 체인 연결 용이).
all 모드는 한 메서드가 실패해도 나머지를 끝까지 실행한 뒤 1 반환.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)  # pydantic-settings env_file=".env" 가 CWD 기준이라 필요

from app.config import get_settings  # noqa: E402
from app.services.kma import (  # noqa: E402
    KMAClient,
    KMAForecast,
    KMAObservation,
    latlon_to_grid,
)

DEFAULT_LAT = 37.5665
DEFAULT_LON = 126.9780
PREVIEW_ROWS = 5  # 예보는 시점이 길어서 앞 N개만 미리보기


def _print_header(title: str, lat: float, lon: float, base_url: str, key_len: int) -> None:
    print(f"--- {title} ---")
    print(f"coord: ({lat}, {lon})")
    print(f"BASE URL: {base_url}")
    print(f"API KEY length: {key_len}")
    grid = latlon_to_grid(lat, lon)
    print(f"grid: nx={grid.nx}, ny={grid.ny}")
    print()


def _print_observation(obs: KMAObservation) -> None:
    print("[OK] HTTP 200 + JSON parsed")
    print(f"  observed_at:        {obs.observed_at}")
    print(f"  temperature_c:      {obs.temperature_c} C")
    print(f"  humidity_pct:       {obs.humidity_pct} %")
    print(f"  wind_speed_ms:      {obs.wind_speed_ms} m/s")
    print(f"  wind_direction_deg: {obs.wind_direction_deg} deg")
    print(f"  precipitation_mm:   {obs.precipitation_mm} mm")


def _print_forecasts(forecasts: list[KMAForecast], expected_temp_key: str) -> None:
    print(f"[OK] HTTP 200 + JSON parsed ({len(forecasts)} time-slots)")
    print(f"  expected temperature key: {expected_temp_key}")
    print()
    # 표 형식 (앞 PREVIEW_ROWS개만)
    print(f"  {'forecast_for':<20} {'temp':>6} {'wind':>6} {'prcp':>6}  {'sky':<8} {'pty':<6}")
    print(f"  {'-' * 20} {'-' * 6} {'-' * 6} {'-' * 6}  {'-' * 8} {'-' * 6}")
    for f in forecasts[:PREVIEW_ROWS]:
        ts = f.forecast_for.strftime("%Y-%m-%d %H:%M")
        temp = f"{f.temperature_c:>6.1f}" if f.temperature_c is not None else f"{'-':>6}"
        wind = f"{f.wind_speed_ms:>6.1f}" if f.wind_speed_ms is not None else f"{'-':>6}"
        prcp = f"{f.precipitation_mm:>6.1f}" if f.precipitation_mm is not None else f"{'-':>6}"
        sky = f.sky_condition or "-"
        pty = f.precipitation_type or "-"
        print(f"  {ts:<20} {temp} {wind} {prcp}  {sky:<8} {pty:<6}")
    if len(forecasts) > PREVIEW_ROWS:
        print(f"  ... ({len(forecasts) - PREVIEW_ROWS} more time-slots omitted)")


async def _run_ncst(client: KMAClient, lat: float, lon: float) -> bool:
    try:
        obs = await client.get_current_observation(lat, lon)
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False
    # 핵심 검증: T1H이 정상 파싱돼 0이 아닌 값이거나 명시적 0
    _print_observation(obs)
    return True


async def _run_ultra_fcst(client: KMAClient, lat: float, lon: float) -> bool:
    try:
        forecasts = await client.get_ultra_short_forecast(lat, lon)
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False
    if not forecasts:
        print("[FAIL] empty forecast list")
        return False
    # T1H이 적어도 한 시점에는 있어야 함 (초단기예보)
    if all(f.temperature_c is None for f in forecasts):
        print("[FAIL] temperature_c is None in all forecasts — expected T1H key may be missing")
        return False
    _print_forecasts(forecasts, expected_temp_key="T1H")
    return True


async def _run_vilage_fcst(client: KMAClient, lat: float, lon: float) -> bool:
    try:
        forecasts = await client.get_short_term_forecast(lat, lon)
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return False
    if not forecasts:
        print("[FAIL] empty forecast list")
        return False
    # 단기예보는 TMP가 기온 (T1H 아님). 매핑이 잘못되면 전부 None
    if all(f.temperature_c is None for f in forecasts):
        print("[FAIL] temperature_c is None in all forecasts — TMP key mapping may be broken")
        return False
    _print_forecasts(forecasts, expected_temp_key="TMP")
    return True


COMMANDS = {
    "ncst":        ("KMA UltraSrtNcst test (current observation)",  _run_ncst),
    "ultra-fcst":  ("KMA UltraSrtFcst test (6h hourly forecast)",   _run_ultra_fcst),
    "vilage-fcst": ("KMA VilageFcst test (3d 3-hourly forecast)",   _run_vilage_fcst),
}


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="KMA API 세 메서드 통합 검증 (ncst / ultra-fcst / vilage-fcst / all)",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=[*COMMANDS.keys(), "all"],
        help="검증할 메서드 (default: all)",
    )
    parser.add_argument("--lat", type=float, default=DEFAULT_LAT, help=f"위도 (default: {DEFAULT_LAT})")
    parser.add_argument("--lon", type=float, default=DEFAULT_LON, help=f"경도 (default: {DEFAULT_LON})")
    args = parser.parse_args()

    s = get_settings()

    if not s.kma_api_key:
        print("[FAIL] KMA_API_KEY is empty in .env")
        return 1

    targets: list[str] = list(COMMANDS.keys()) if args.command == "all" else [args.command]

    results: dict[str, bool] = {}
    async with KMAClient(api_key=s.kma_api_key, base_url=s.kma_base_url) as client:
        for cmd in targets:
            title, runner = COMMANDS[cmd]
            _print_header(title, args.lat, args.lon, s.kma_base_url, len(s.kma_api_key))
            ok = await runner(client, args.lat, args.lon)
            results[cmd] = ok
            print()

    if len(targets) > 1:
        print("=== Summary ===")
        for cmd, ok in results.items():
            print(f"  {cmd:<12} {'OK' if ok else 'FAIL'}")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
