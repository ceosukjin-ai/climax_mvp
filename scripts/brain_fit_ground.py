#!/usr/bin/env python3
"""뇌 v1 Day 2-①: 지면온도 계수 자동 교정 (engine_check ASOS 짝, dry-run).

Day 1 리포트가 찾은 약점 — ASOS 개활지 지면온도가 정오 +5.9 / 야간 −2.2 (열관성 부족).
현재 수지식은 정상상태(낮에 저장한 열을 밤에 내놓는 항이 없음). 여기서는
  · 기존 계수 3개: hc_a, hc_b, ground_storage_fraction(f_stor)
  · 신규 항 1개  : ground_release_wm2(q_rel) — 지중 저장열 방출 [W/m²], 일사가 약할수록 켜짐
                   G_rel = q_rel · max(0, 1 − S↓/200)
를 engine_check 90일 짝(관측소 = 개활지, SVF 1, 알베도 0.20)에 격자 탐색으로 맞춘다.
엔진은 건드리지 않는다 — 결과는 brain_version(kind='ground', promoted=false) 와 stdout.
승격(엔진 반영)은 사람이 리포트 보고 결정(Day 2-③).

실행(서버 컨테이너): ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/brain_fit_ground.py
"""
from __future__ import annotations
import asyncio, itertools, json, math, os, sys
from datetime import timezone, timedelta
import numpy as np

sys.path.insert(0, "/app")
from vpti_core.solar import estimate_solar            # noqa: E402
from vpti_core.mrt import sky_emissivity, STEFAN_BOLTZMANN, KELVIN  # noqa: E402
from vpti_core import DEFAULT_CONFIG                  # noqa: E402

KST = timezone(timedelta(hours=9))
ALBEDO, EPS_G, S0 = 0.20, 0.95, 200.0
GRID = {"hc_a": [6, 8, 10, 12, 14, 16, 18, 20], "hc_b": [2, 3, 4, 5, 6],
        "f_stor": [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6],
        "q_rel": [0, 10, 20, 30, 40, 50, 60, 80]}


def band(h):
    return "야간" if h < 6 or h >= 18 else "오전" if h < 11 else "정오" if h < 14 else "오후"


def solve(ta, sw_abs, l_down, u, hc_a, hc_b, f_stor, q_rel, s_down):
    """벡터 Newton — 행별 Ts"""
    avail = sw_abs * (1 - f_stor) + q_rel * np.clip(1 - s_down / S0, 0, 1)
    h = hc_a + hc_b * u
    ts = ta.copy()
    for _ in range(25):
        tk = ts + KELVIN
        f = avail + EPS_G * (l_down - STEFAN_BOLTZMANN * tk ** 4) - h * (ts - ta)
        fp = -4 * EPS_G * STEFAN_BOLTZMANN * tk ** 3 - h
        ts = ts - f / fp
    return ts


async def main():
    import asyncpg
    from app.config import get_settings
    from app.services.orchestrator import ASOS_STATIONS
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    rows = await conn.fetch(
        "SELECT observed_at, station_id, obs_ground_c, air_temp, wind_ms, est_cloud, obs_cloud "
        "FROM engine_check WHERE observed_at > NOW() - INTERVAL '90 days' "
        "AND obs_ground_c IS NOT NULL AND air_temp IS NOT NULL AND station_id IS NOT NULL")
    ta, u, obs, sdn, ldn, hrs = [], [], [], [], [], []
    for r in rows:
        if r["station_id"] not in ASOS_STATIONS:
            continue
        lat, lon = ASOS_STATIONS[r["station_id"]]
        cf = r["est_cloud"] if r["est_cloud"] is not None else (r["obs_cloud"] if r["obs_cloud"] is not None else 0.3)
        t = r["observed_at"]
        sun = estimate_solar(lat, lon, t, cloud_fraction=float(cf))
        beta = math.radians(max(sun.solar_elevation_deg, 0.0))
        s_down = max(sun.dni * math.sin(beta) + sun.dhi, 0.0)
        eps_sky = sky_emissivity(float(r["air_temp"]), 60.0, sun.cloud_fraction)
        ta.append(float(r["air_temp"])); u.append(float(r["wind_ms"] or 0.5)); obs.append(float(r["obs_ground_c"]))
        sdn.append(s_down); ldn.append(STEFAN_BOLTZMANN * (float(r["air_temp"]) + KELVIN) ** 4 * eps_sky)
        hrs.append(t.astimezone(KST).hour)
    ta, u, obs, sdn, ldn = map(np.array, (ta, u, obs, sdn, ldn)); hrs = np.array(hrs)
    sw_abs = (1 - ALBEDO) * sdn
    n = len(obs); print(f"engine_check 짝 {n}건 (90일, 관측소 {len(set(r['station_id'] for r in rows))}곳)")
    bands = np.array([band(h) for h in hrs])

    def report(name, ts):
        e = ts - obs
        line = f"{name:34s} MAE {np.mean(np.abs(e)):5.2f} bias {np.mean(e):+5.2f}  "
        line += "  ".join(f"{b} {np.mean(e[bands == b]):+.1f}" for b in ("오전", "정오", "오후", "야간") if (bands == b).any())
        print(line); return float(np.mean(np.abs(e)))

    c = DEFAULT_CONFIG.mrt
    base = dict(hc_a=c.hc_a, hc_b=c.hc_b, f_stor=c.ground_storage_fraction, q_rel=getattr(c, "ground_release_wm2", 0.0))
    print("\n[현재 엔진 계수]", base)
    mae0 = report("현재", solve(ta, sw_abs, ldn, u, base["hc_a"], base["hc_b"], base["f_stor"], base["q_rel"], sdn))

    print("\n[격자 탐색 — 기존 3계수만 (q_rel=0)]")
    best3 = min(((np.mean(np.abs(solve(ta, sw_abs, ldn, u, a, b, f, 0, sdn) - obs)), a, b, f)
                 for a, b, f in itertools.product(GRID["hc_a"], GRID["hc_b"], GRID["f_stor"])), key=lambda x: x[0])
    p3 = dict(hc_a=best3[1], hc_b=best3[2], f_stor=best3[3], q_rel=0)
    report(f"3계수 최적 {p3}", solve(ta, sw_abs, ldn, u, **{k: p3[k] for k in ('hc_a', 'hc_b', 'f_stor', 'q_rel')}, s_down=sdn))

    print("\n[격자 탐색 — 방출항 포함 4계수]")
    best4 = min(((np.mean(np.abs(solve(ta, sw_abs, ldn, u, a, b, f, q, sdn) - obs)), a, b, f, q)
                 for a, b, f, q in itertools.product(GRID["hc_a"], GRID["hc_b"], GRID["f_stor"], GRID["q_rel"])), key=lambda x: x[0])
    p4 = dict(hc_a=best4[1], hc_b=best4[2], f_stor=best4[3], q_rel=best4[4])
    mae4 = report(f"4계수 최적 {p4}", solve(ta, sw_abs, ldn, u, p4["hc_a"], p4["hc_b"], p4["f_stor"], p4["q_rel"], sdn))

    # 시간 분할 검증(앞 60일 학습 → 뒤 30일 검증)으로 과적합 확인
    order = np.argsort([r["observed_at"] for r in rows if r["station_id"] in ASOS_STATIONS])
    cut = int(n * 2 / 3); tr, te = order[:cut], order[cut:]
    bt = min(((np.mean(np.abs(solve(ta[tr], sw_abs[tr], ldn[tr], u[tr], a, b, f, q, sdn[tr]) - obs[tr])), a, b, f, q)
              for a, b, f, q in itertools.product(GRID["hc_a"], GRID["hc_b"], GRID["f_stor"], GRID["q_rel"])), key=lambda x: x[0])
    e_te = solve(ta[te], sw_abs[te], ldn[te], u[te], bt[1], bt[2], bt[3], bt[4], sdn[te]) - obs[te]
    e_te0 = solve(ta[te], sw_abs[te], ldn[te], u[te], base["hc_a"], base["hc_b"], base["f_stor"], base["q_rel"], sdn[te]) - obs[te]
    print(f"\n[시간분할 검증] 앞 {cut}건 학습 → 뒤 {n-cut}건: 현재 MAE {np.mean(np.abs(e_te0)):.2f} → 학습계수 {np.mean(np.abs(e_te)):.2f} "
          f"(학습계수 {dict(hc_a=bt[1], hc_b=bt[2], f_stor=bt[3], q_rel=bt[4])})")

    metrics = {"n": n, "mae_current": round(mae0, 3), "mae_fit3": round(float(best3[0]), 3), "mae_fit4": round(mae4, 3),
               "holdout_current": round(float(np.mean(np.abs(e_te0))), 3), "holdout_fit": round(float(np.mean(np.abs(e_te))), 3),
               "band_bias_fit4": {b: round(float(np.mean((solve(ta, sw_abs, ldn, u, p4['hc_a'], p4['hc_b'], p4['f_stor'], p4['q_rel'], sdn) - obs)[bands == b])), 2)
                                  for b in ("오전", "정오", "오후", "야간") if (bands == b).any()}}
    await conn.execute("INSERT INTO brain_version (kind, params, metrics, n_train, promoted, reason) VALUES ($1,$2,$3,$4,FALSE,$5)",
                       "ground", json.dumps(p4), json.dumps(metrics, ensure_ascii=False), n, "day2 fit, 승격 대기(사람 확인)")
    await conn.close()
    print("\nbrain_version 에 kind='ground' 후보 기록 (promoted=false). 엔진 변경 없음.")


if __name__ == "__main__":
    asyncio.run(main())
