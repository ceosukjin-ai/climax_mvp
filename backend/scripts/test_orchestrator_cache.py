"""
VPTIOrchestrator 캐싱 동작 통합 검증 스크립트.

핵심 불변성 (CLAUDE.md):
  공간 데이터(panoId 단위)와 기상 데이터(격자 단위)는 분리 캐시.
  재방문은 <100ms 안에 응답해야 한다.

호출 시나리오 (4회):
  1. cold : 서울시청 (37.5665, 126.9780)            — 둘 다 miss 예상
  2. hot  : 같은 좌표 즉시 재호출                       — 둘 다 hit, <100ms 검증
  3. near : (37.5667, 126.9782) ~25m 이동             — location key 다름.
            panoId가 같으면 pano:analysis hit, weather grid 같으면 weather hit.
  4. far  : (37.5750, 126.9783) ~1km 북상              — panoId 다를 가능성 높음.
            pano:analysis miss → SegFormer 추론 다시 일어남.

각 호출 후 PipelineTelemetry 출력 + 마지막에 Redis 키 덤프 (pano:* / weather:*).

실행:
    set PYTHONIOENCODING=utf-8
    python backend/scripts/test_orchestrator_cache.py
    python backend/scripts/test_orchestrator_cache.py --clear-cache  # 시작 전 전부 비움

종료 코드: 0 = hot 호출이 <100ms 통과, 1 = hot 호출이 100ms 초과 또는 다른 실패.

용도: orchestrator 파이프라인의 캐시 분리 설계가 실제로
'재방문 <100ms'를 달성하는지 회귀 검증. 모델 로드는 캐시에 안 들어가므로
별도 측정 (cold 호출 직전에 한 번 일어남).

이 스크립트는 정식 테스트가 아니라 수동 진단용. tests/ 아래로 옮기지 말 것.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app.config import get_settings  # noqa: E402
from app.ml.segformer import get_segformer_service  # noqa: E402
from app.services.cache import CacheService  # noqa: E402
from app.services.kma import KMAClient  # noqa: E402
from app.services.orchestrator import (  # noqa: E402
    PipelineTelemetry,
    VPTIOrchestrator,
)
from app.services.street_view import GoogleStreetViewClient  # noqa: E402

# 시나리오 좌표
SEOUL_CITY_HALL = (37.5665, 126.9780)
NEAR_COORD = (37.5667, 126.9782)        # ~25m 북동
FAR_COORD = (37.5750, 126.9783)         # ~1km 북상

HOT_THRESHOLD_MS = 100.0


@dataclass(frozen=True)
class CallResult:
    label: str
    lat: float
    lon: float
    telemetry: PipelineTelemetry
    vpti: float
    error: str | None = None


def _print_telemetry(label: str, lat: float, lon: float, t: PipelineTelemetry) -> None:
    pano_state = "HIT" if t.pano_cache_hit else "MISS"
    weather_state = "HIT" if t.weather_cache_hit else "MISS"
    print(f"  [{label}] ({lat}, {lon})")
    print(f"    pano cache    : {pano_state}")
    print(f"    weather cache : {weather_state}")
    print(f"    total         : {t.total_ms:.1f} ms")
    if not t.pano_cache_hit:
        print(f"    street_view   : {t.street_view_ms:.1f} ms")
        print(f"    segmentation  : {t.segmentation_ms:.1f} ms")
    if not t.weather_cache_hit:
        print(f"    weather fetch : {t.weather_ms:.1f} ms")


async def _dump_keys(cache: CacheService, pattern: str, max_show: int = 20) -> list[str]:
    keys: list[str] = []
    async for key in cache._client.scan_iter(match=pattern, count=1000):
        keys.append(key.decode() if isinstance(key, bytes) else key)
    keys.sort()
    print(f"  pattern '{pattern}' → {len(keys)} key(s)")
    for k in keys[:max_show]:
        print(f"    {k}")
    if len(keys) > max_show:
        print(f"    ... ({len(keys) - max_show} more)")
    return keys


async def _build_orchestrator() -> tuple[CacheService, VPTIOrchestrator, GoogleStreetViewClient, KMAClient]:
    """main.py:lifespan()과 동일한 조립 — SegFormer 포함."""
    s = get_settings()
    if not s.google_streetview_api_key or not s.kma_api_key:
        raise RuntimeError(
            "GOOGLE_STREETVIEW_API_KEY / KMA_API_KEY missing in .env — "
            "orchestrator cannot be assembled."
        )

    cache = CacheService(redis_url=s.redis_url)
    if not await cache.ping():
        raise RuntimeError(
            f"Redis ping failed at {s.redis_url} — is 'docker compose up -d' running?"
        )

    sv_client = GoogleStreetViewClient(
        api_key=s.google_streetview_api_key,
        signing_secret=s.google_streetview_signing_secret,
    )
    kma_client = KMAClient(api_key=s.kma_api_key, base_url=s.kma_base_url)

    print("[setup] Loading SegFormer (first time can take ~20s)...")
    seg_start = asyncio.get_event_loop().time()
    segformer = get_segformer_service()
    segformer.load()
    seg_load_ms = (asyncio.get_event_loop().time() - seg_start) * 1000
    print(f"[setup] SegFormer loaded in {seg_load_ms:.0f} ms (not counted in cold call)")

    orchestrator = VPTIOrchestrator(
        cache=cache,
        street_view=sv_client,
        kma=kma_client,
        segformer=segformer,
    )
    return cache, orchestrator, sv_client, kma_client


async def _call(
    orch: VPTIOrchestrator, label: str, lat: float, lon: float
) -> CallResult:
    try:
        result, telemetry = await orch.compute(lat, lon)
    except Exception as e:
        # 시나리오 도중 panoId 미존재 등으로 실패해도 다음 호출은 계속
        import traceback
        print(f"  [{label}] ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        return CallResult(
            label=label, lat=lat, lon=lon,
            telemetry=PipelineTelemetry(False, False, 0.0, 0.0, 0.0, 0.0),
            vpti=float("nan"),
            error=str(e),
        )
    _print_telemetry(label, lat, lon, telemetry)
    print(
        f"    VPTI={result.vpti:.1f}C  "
        f"(Δspace={result.contribution_space:+.2f}  "
        f"Δmat={result.contribution_material:+.2f}  "
        f"Δwind={result.contribution_wind:+.2f})"
    )
    return CallResult(
        label=label, lat=lat, lon=lon, telemetry=telemetry,
        vpti=result.vpti,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="VPTIOrchestrator 캐시 동작 검증 (cold/hot/near/far)"
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="시작 전 pano:*, weather:* 키 모두 삭제 (진정한 cold 측정용)",
    )
    args = parser.parse_args()

    cache, orch, sv_client, kma_client = await _build_orchestrator()

    try:
        if args.clear_cache:
            print("\n[setup] Clearing pano:* and weather:* keys...")
            n1 = await cache.clear_namespace("pano:*")
            n2 = await cache.clear_namespace("weather:*")
            print(f"[setup] Cleared {n1 + n2} keys (pano={n1}, weather={n2})")

        print("\n=== Initial Redis state ===")
        await _dump_keys(cache, "pano:*", max_show=10)
        await _dump_keys(cache, "weather:*", max_show=10)

        print("\n=== Call 1: cold (Seoul City Hall) ===")
        r1 = await _call(orch, "cold", *SEOUL_CITY_HALL)

        print("\n=== Call 2: hot (same coord) ===")
        r2 = await _call(orch, "hot ", *SEOUL_CITY_HALL)

        print(f"\n=== Call 3: near (~25m: {NEAR_COORD}) ===")
        r3 = await _call(orch, "near", *NEAR_COORD)

        print(f"\n=== Call 4: far  (~1km: {FAR_COORD}) ===")
        r4 = await _call(orch, "far ", *FAR_COORD)

        print("\n=== Final Redis state ===")
        await _dump_keys(cache, "pano:analysis:*", max_show=10)
        await _dump_keys(cache, "pano:location:*", max_show=10)
        await _dump_keys(cache, "weather:*", max_show=10)

        # 판정
        print("\n=== Verdict ===")
        all_results = [r1, r2, r3, r4]
        for r in all_results:
            tag = "FAIL" if r.error else "OK  "
            print(
                f"  [{tag}] {r.label}: total={r.telemetry.total_ms:6.1f}ms  "
                f"pano={'HIT' if r.telemetry.pano_cache_hit else 'MISS'}  "
                f"wx={'HIT' if r.telemetry.weather_cache_hit else 'MISS'}"
            )

        # 핵심 검증: hot 호출이 <100ms
        hot_ms = r2.telemetry.total_ms
        hot_ok = (
            not r2.error
            and r2.telemetry.pano_cache_hit
            and r2.telemetry.weather_cache_hit
            and hot_ms < HOT_THRESHOLD_MS
        )
        print()
        if hot_ok:
            print(f"[PASS] hot revisit {hot_ms:.1f}ms < {HOT_THRESHOLD_MS}ms threshold")
            return 0
        else:
            print(
                f"[FAIL] hot revisit {hot_ms:.1f}ms (threshold {HOT_THRESHOLD_MS}ms) "
                f"| pano_hit={r2.telemetry.pano_cache_hit} "
                f"weather_hit={r2.telemetry.weather_cache_hit}"
            )
            return 1
    finally:
        await sv_client.close()
        await kma_client.close()
        await cache.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
