"""지면온도 계수 교정 — engine_check 잔차로 f_stor·h_c 피팅 (2026-08-16).

무엇을 하나
-----------
orchestrator 가 ASOS 정시 관측 때마다 DB(engine_check)에 적재하는
  · obs_ground_c  : 관측소 실측 지면온도 (직접 측정)
  · air_temp, wind_ms, est_cloud, observed_at, station_id
를 읽어, 엔진의 표면 에너지수지(estimate_ground_temp)를 계수 조합별로 다시 돌려
실측과의 RMSE 가 최소가 되는 (f_stor, h_c 배율) 을 격자 탐색으로 찾는다.

scipy 불필요(격자 탐색) — 서버 컨테이너/venv 의 기존 의존성만 사용.

사용법 (서버에서, 데이터 1~2주 쌓인 뒤)
---------------------------------------
  cd ~/climax_mvp/backend
  DATABASE_URL=... python scripts/fit_tsurf.py          # 환경변수로
  python scripts/fit_tsurf.py --min-n 100               # 최소 표본 수 지정

출력
----
  · 표본 수 / 현재 계수 RMSE·bias vs 최적 계수 RMSE·bias
  · 주간/야간 분리 잔차 (f_stor 는 주간, 장파·h_c 는 야간을 지배 — 분리 확인 필수)
  · 권장 계수와, vpti_core/config.py 에서 바꿀 줄

⚠️ 결과 적용은 수동으로 — 계수를 바꾸면 pVPTI 절대값이 이동하므로
   현장실측 문서의 검증값과 재대조 후 config.py 를 갱신할 것.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from vpti_core.config import DEFAULT_CONFIG  # noqa: E402
from vpti_core.mrt import estimate_ground_temp, sky_emissivity  # noqa: E402
from vpti_core.solar import estimate_solar  # noqa: E402
from app.services.kma import ASOS_STATIONS  # noqa: E402

# 관측소 개활지 가정 (orchestrator 적재 시와 동일해야 비교가 성립한다)
ALBEDO, EMISSIVITY, SVF, GVI = 0.20, 0.95, 1.0, 0.0

# 격자 탐색 범위
F_STOR_GRID = [round(0.05 * i, 2) for i in range(1, 13)]     # 0.05 ~ 0.60
HC_SCALE_GRID = [round(0.6 + 0.1 * i, 1) for i in range(11)]  # 0.6 ~ 1.6


async def load_rows(url: str) -> list[dict]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        res = await conn.execute(text(
            "SELECT observed_at, station_id, obs_ground_c, est_cloud, obs_cloud,"
            "       air_temp, wind_ms "
            "FROM engine_check "
            "WHERE obs_ground_c IS NOT NULL AND air_temp IS NOT NULL "
            "ORDER BY observed_at"
        ))
        rows = [dict(r._mapping) for r in res]
    await engine.dispose()
    return rows


def predict(row: dict, f_stor: float, hc_scale: float) -> float | None:
    stn = row["station_id"]
    if stn not in ASOS_STATIONS:
        return None
    slat, slon = ASOS_STATIONS[stn]
    cf = row["est_cloud"] if row["est_cloud"] is not None else row["obs_cloud"]
    solar = estimate_solar(slat, slon, row["observed_at"], cloud_fraction=cf)
    eps_sky = sky_emissivity(row["air_temp"], 50.0, solar.cloud_fraction)
    cfg = dataclasses.replace(
        DEFAULT_CONFIG.mrt,
        ground_storage_fraction=f_stor,
        hc_a=DEFAULT_CONFIG.mrt.hc_a * hc_scale,
        hc_b=DEFAULT_CONFIG.mrt.hc_b * hc_scale,
    )
    return estimate_ground_temp(
        air_temp_c=row["air_temp"], solar=solar,
        ground_albedo=ALBEDO, ground_emissivity=EMISSIVITY,
        svf=SVF, gvi=GVI,
        wind_ms=row["wind_ms"] or 0.5, eps_sky=eps_sky, config=cfg,
    ), solar.is_daytime


def evaluate(rows: list[dict], f_stor: float, hc_scale: float):
    """(RMSE, bias, n, 주간 RMSE, 야간 RMSE)"""
    sq = bias = 0.0
    day_sq, day_n, night_sq, night_n = 0.0, 0, 0.0, 0
    n = 0
    for row in rows:
        out = predict(row, f_stor, hc_scale)
        if out is None:
            continue
        pred, is_day = out
        r = pred - row["obs_ground_c"]
        sq += r * r
        bias += r
        n += 1
        if is_day:
            day_sq += r * r; day_n += 1
        else:
            night_sq += r * r; night_n += 1
    if n == 0:
        return None
    return (
        math.sqrt(sq / n), bias / n, n,
        math.sqrt(day_sq / day_n) if day_n else float("nan"),
        math.sqrt(night_sq / night_n) if night_n else float("nan"),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=50,
                    help="이보다 표본이 적으면 피팅하지 않는다 (기본 50 ≈ 2일치)")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit("DATABASE_URL 환경변수가 필요합니다 (.env.prod 의 값)")

    rows = asyncio.run(load_rows(url))
    print(f"engine_check 짝 데이터: {len(rows)}건")
    if len(rows) < args.min_n:
        sys.exit(f"표본 {len(rows)} < {args.min_n} — 더 쌓인 뒤 다시 실행하세요")

    cur_f = DEFAULT_CONFIG.mrt.ground_storage_fraction
    base = evaluate(rows, cur_f, 1.0)
    print(f"\n[현재 계수] f_stor={cur_f}, h_c×1.0")
    print(f"  RMSE {base[0]:.2f}°C · bias {base[1]:+.2f}°C · n={base[2]}"
          f" · 주간 {base[3]:.2f} · 야간 {base[4]:.2f}")

    best, best_key = None, None
    for f in F_STOR_GRID:
        for s in HC_SCALE_GRID:
            ev = evaluate(rows, f, s)
            if ev and (best is None or ev[0] < best[0]):
                best, best_key = ev, (f, s)

    f, s = best_key
    print(f"\n[최적 계수] f_stor={f}, h_c×{s}")
    print(f"  RMSE {best[0]:.2f}°C · bias {best[1]:+.2f}°C"
          f" · 주간 {best[3]:.2f} · 야간 {best[4]:.2f}")
    print(f"  개선: RMSE {base[0]:.2f} → {best[0]:.2f} ({base[0]-best[0]:+.2f})")
    print("\nvpti_core/config.py 반영 줄:")
    print(f"  ground_storage_fraction = {f}")
    print(f"  hc_a = {DEFAULT_CONFIG.mrt.hc_a * s:.1f}   # 기본 {DEFAULT_CONFIG.mrt.hc_a} × {s}")
    print(f"  hc_b = {DEFAULT_CONFIG.mrt.hc_b * s:.1f}   # 기본 {DEFAULT_CONFIG.mrt.hc_b} × {s}")
    print("\n⚠️ 적용 전 현장실측 문서의 검증 시나리오와 재대조할 것 (pVPTI 절대값 이동).")


if __name__ == "__main__":
    main()
