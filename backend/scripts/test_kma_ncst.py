"""
KMA 초단기실황(getUltraSrtNcst) 호출 검증 스크립트.

실행:
    set PYTHONIOENCODING=utf-8     # Windows cp949 콘솔에서 print 깨짐 방지
    python backend/scripts/test_kma_ncst.py

CWD가 어디든 동작합니다 (스크립트가 backend/를 sys.path에 자동 추가).

기본 좌표는 서울시청(37.5665, 126.9780). 다른 좌표를 검증하려면
파일 하단의 LAT/LON 상수만 수정.

용도: 공공데이터포털 API 키 발급 직후, KMA 채널/엔드포인트 전환 후,
또는 응답 파싱 회귀가 의심될 때 KMA 한 호출만 격리해서 빠르게 검증.
orchestrator를 거치지 않으므로 Google Street View나 SegFormer가
실패해도 무관하게 KMA 단독으로 진단 가능.

종료 코드: 0 = 성공, 1 = 실패 (CI/스크립트 체인 연결 용이).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)  # pydantic-settings env_file=".env" 가 CWD 기준이라 필요

from app.config import get_settings  # noqa: E402
from app.services.kma import KMAClient, latlon_to_grid  # noqa: E402

LAT = 37.5665
LON = 126.9780


async def main() -> int:
    s = get_settings()

    print("--- KMA UltraSrtNcst test ---")
    print(f"coord: ({LAT}, {LON})")
    print(f"BASE URL: {s.kma_base_url}")
    print(f"API KEY length: {len(s.kma_api_key)}")
    grid = latlon_to_grid(LAT, LON)
    print(f"grid: nx={grid.nx}, ny={grid.ny}")
    print()

    if not s.kma_api_key:
        print("[FAIL] KMA_API_KEY is empty in .env")
        return 1

    async with KMAClient(api_key=s.kma_api_key, base_url=s.kma_base_url) as client:
        try:
            obs = await client.get_current_observation(LAT, LON)
        except Exception as e:
            print(f"[FAIL] {type(e).__name__}: {e}")
            return 1

    print("[OK] HTTP 200 + JSON parsed")
    print(f"  observed_at:        {obs.observed_at}")
    print(f"  temperature_c:      {obs.temperature_c} C")
    print(f"  humidity_pct:       {obs.humidity_pct} %")
    print(f"  wind_speed_ms:      {obs.wind_speed_ms} m/s")
    print(f"  wind_direction_deg: {obs.wind_direction_deg} deg")
    print(f"  precipitation_mm:   {obs.precipitation_mm} mm")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
