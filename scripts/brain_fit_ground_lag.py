#!/usr/bin/env python3
"""뇌 v1 Day 2-①b: 지면온도 **시간지연(열관성)** 항 fit — engine_check ASOS 짝 (dry-run).

①(계수만)은 시간분할 검증에서 탈락: 오전 +1.9 / 오후 −1.9 위상 오차 = 정상상태 수지식의 구조 한계.
여기서는 순간 일사 S↓(t) 대신 **지난 8시간 일사의 지수가중 평균**을 쓴다:
    S_eff(t) = Σ S↓(t−u)·w(u),  w(u) ∝ e^(−u/τ),  u = 0, 20분, …, 8h
τ=0 이면 현재 식과 동일. ASOS 짝이 띄엄띄엄이라도 과거 일사는 pvlib으로 언제든 계산되므로 적용 가능.
엔진 실시간 적용도 같은 방식(과거 8h 태양위치는 좌표·시각만으로 계산, 운량은 현재값).

fit 대상: hc_a, hc_b, f_stor, q_rel(야간 방출), τ.  검증: 앞 2/3 학습 → 뒤 1/3.
엔진은 건드리지 않음 — brain_version(kind='ground_lag', promoted=false) 기록.

실행(서버 컨테이너): ... run --rm -v $HOME/climax_mvp:/repo api python3 /repo/scripts/brain_fit_ground_lag.py
"""
from __future__ import annotations
import asyncio, itertools, json, math, sys, time
from datetime import timezone, timedelta
import numpy as np

sys.path.insert(0, "/app")
from vpti_core.solar import estimate_solar            # noqa: E402
from vpti_core.mrt import sky_emissivity, STEFAN_BOLTZMANN, KELVIN  # noqa: E402
from vpti_core import DEFAULT_CONFIG                  # noqa: E402

KST = timezone(timedelta(hours=9))
ALBEDO, EPS_G, S0 = 0.20, 0.95, 200.0
STEP_MIN, HOURS = 20, 8
LAGS = np.arange(0, HOURS * 60 + 1, STEP_MIN) / 60.0          # h
GRID = {"hc_a": [8, 12, 16, 20], "hc_b": [2, 4, 6], "f_stor": [0.2, 0.3, 0.4, 0.5],
        "q_rel": [0, 20, 40, 60], "tau": [0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]}
if "--fix-hc" in sys.argv:
    # (a) 아스팔트 80점 열화상으로 맞춘 대류계수(hc_a 12·hc_b 4)는 고정 — 잔디 관측소가 증발산을 대류로 떠안는 것 방지.
    #     구조 항(저장비율·야간방출·시정수)만 ASOS 로 학습.
    GRID = {"hc_a": [12], "hc_b": [4], "f_stor": [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5],
            "q_rel": [0, 10, 20, 30, 40, 60, 80], "tau": [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0]}


def band(h):
    return "야간" if h < 6 or h >= 18 else "오전" if h < 11 else "정오" if h < 14 else "오후"


def s_eff(S, tau):
    """S: (n, len(LAGS)) 과거 일사 샘플(0열=현재). tau=0 → 현재값."""
    if tau <= 0:
        return S[:, 0]
    w = np.exp(-LAGS / tau); w /= w.sum()
    return S @ w


def solve(ta, seff, l_down, u, hc_a, hc_b, f_stor, q_rel):
    sw_abs = (1 - ALBEDO) * seff
    avail = sw_abs * (1 - f_stor) + q_rel * np.clip(1 - seff / S0, 0, 1)
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
        "AND obs_ground_c IS NOT NULL AND air_temp IS NOT NULL AND station_id IS NOT NULL ORDER BY observed_at")
    rows = [r for r in rows if r["station_id"] in ASOS_STATIONS]
    n = len(rows)
    stn = np.array([int(r["station_id"]) for r in rows]); days = np.array([r["observed_at"].astimezone(KST).strftime("%Y-%m-%d") for r in rows])
    CACHE = "/repo/data/brain_ground_lag_cache.npz"
    import os
    cached = os.path.exists(CACHE) and int(np.load(CACHE)["n"]) == n
    if cached:
        z = np.load(CACHE); S, ta, u, obs, ldn, hrs = z["S"], z["ta"], z["u"], z["obs"], z["ldn"], z["hrs"]
        print(f"engine_check 짝 {n}건 (일사 샘플 캐시 사용)")
    else:
        print(f"engine_check 짝 {n}건, 과거 {HOURS}h 일사 샘플 {len(LAGS)}개/행 계산 중…", flush=True)
    t0 = time.time()
    if not cached:
      ta, u, obs, ldn, hrs = [], [], [], [], []
      S = np.zeros((n, len(LAGS)))
      for k, r in enumerate(rows):
          lat, lon = ASOS_STATIONS[r["station_id"]]  # noqa: E111
          cf = r["est_cloud"] if r["est_cloud"] is not None else (r["obs_cloud"] if r["obs_cloud"] is not None else 0.3)
          t = r["observed_at"]
          for j, lag in enumerate(LAGS):
              sun = estimate_solar(lat, lon, t - timedelta(hours=float(lag)), cloud_fraction=float(cf))
              beta = math.radians(max(sun.solar_elevation_deg, 0.0))
              S[k, j] = max(sun.dni * math.sin(beta) + sun.dhi, 0.0)
              if j == 0:
                  eps_sky = sky_emissivity(float(r["air_temp"]), 60.0, sun.cloud_fraction)
          ta.append(float(r["air_temp"])); u.append(float(r["wind_ms"] or 0.5)); obs.append(float(r["obs_ground_c"]))
          ldn.append(STEFAN_BOLTZMANN * (float(r["air_temp"]) + KELVIN) ** 4 * eps_sky)
          hrs.append(t.astimezone(KST).hour)
          if (k + 1) % 300 == 0:
              print(f"  {k+1}/{n} ({time.time()-t0:.0f}s)", flush=True)
      ta, u, obs, ldn, hrs = map(np.array, (ta, u, obs, ldn, hrs))
      np.savez(CACHE, S=S, ta=ta, u=u, obs=obs, ldn=ldn, hrs=hrs, n=n)
    bands = np.array([band(int(h)) for h in hrs])
    SE = {tau: s_eff(S, tau) for tau in GRID["tau"]}

    def report(name, ts):
        e = ts - obs
        print(f"{name:52s} MAE {np.mean(np.abs(e)):5.2f} bias {np.mean(e):+5.2f}  " +
              "  ".join(f"{b} {np.mean(e[bands == b]):+.1f}" for b in ("오전", "정오", "오후", "야간") if (bands == b).any()))
        return float(np.mean(np.abs(e)))

    c = DEFAULT_CONFIG.mrt
    base = (c.hc_a, c.hc_b, c.ground_storage_fraction, 0.0, 0.0)
    print("\n[현재 엔진] hc_a %.0f hc_b %.0f f_stor %.2f q_rel 0 tau 0" % base[:3])
    mae0 = report("현재", solve(ta, SE[0], ldn, u, *base[:4]))

    def search(idx):
        best = None
        for a, b, f, q, tau in itertools.product(*GRID.values()):
            e = np.mean(np.abs(solve(ta[idx], SE[tau][idx], ldn[idx], u[idx], a, b, f, q) - obs[idx]))
            if best is None or e < best[0]:
                best = (e, a, b, f, q, tau)
        return best
    allidx = np.arange(n)
    print("\n[격자 탐색 — 전체] (%d 조합)" % (len(list(itertools.product(*GRID.values())))), flush=True)
    bA = search(allidx)
    pA = dict(zip(("hc_a", "hc_b", "f_stor", "q_rel", "tau"), bA[1:]))
    maeA = report(f"최적 {pA}", solve(ta, SE[pA['tau']], ldn, u, pA["hc_a"], pA["hc_b"], pA["f_stor"], pA["q_rel"]))
    # τ만 바꿨을 때(현재 계수 유지) — 시간지연 단독 효과
    bT = min(((np.mean(np.abs(solve(ta, SE[tau], ldn, u, *base[:4]) - obs)), tau) for tau in GRID["tau"]))
    report(f"현재 계수 + tau={bT[1]}h 만", solve(ta, SE[bT[1]], ldn, u, *base[:4]))

    def fit_eval(tr, te, label):
        b = search(tr); p = dict(zip(("hc_a", "hc_b", "f_stor", "q_rel", "tau"), b[1:]))
        e1 = solve(ta[te], SE[p["tau"]][te], ldn[te], u[te], p["hc_a"], p["hc_b"], p["f_stor"], p["q_rel"]) - obs[te]
        e0 = solve(ta[te], SE[0][te], ldn[te], u[te], *base[:4]) - obs[te]
        bb = "  ".join(f"{x} {np.mean(e1[bands[te] == x]):+.1f}" for x in ("오전", "정오", "오후", "야간") if (bands[te] == x).any())
        print(f"  {label:26s} n={len(te):4d}  현재 {np.mean(np.abs(e0)):.2f} → 학습 {np.mean(np.abs(e1)):.2f} (bias {np.mean(e1):+.2f})  {bb}  {p}")
        return float(np.mean(np.abs(e0))), float(np.mean(np.abs(e1))), float(np.mean(e1)), p

    # 관측소별 편향 (현재 vs 전체최적) — 센서 밑 표면 차이 진단
    eC = solve(ta, SE[0], ldn, u, *base[:4]) - obs
    eF = solve(ta, SE[pA['tau']], ldn, u, pA["hc_a"], pA["hc_b"], pA["f_stor"], pA["q_rel"]) - obs
    print("\n[관측소별] n / 현재 bias / 최적 bias / 기간")
    for sid in sorted(set(stn.tolist())):
        m = stn == sid
        print(f"  stn {sid:3d}  n={m.sum():4d}  현재 {np.mean(eC[m]):+5.1f}  최적 {np.mean(eF[m]):+5.1f}   {min(days[m].tolist())}~{max(days[m].tolist())}")

    print("\n[검증 ① 시간분할 앞2/3→뒤1/3]")
    cut = int(n * 2 / 3)
    print(f"  학습 {days[0]}~{days[cut-1]} / 검증 {days[cut]}~{days[-1]}  (검증 관측소 {sorted(set(stn[cut:].tolist()))})")
    h0t, h1t, bt_, pT = fit_eval(allidx[:cut], allidx[cut:], "시간분할")
    print("[검증 ② 날짜 무작위 분할(짝수/홀수 일)]")
    ud = sorted(set(days.tolist())); odd = np.isin(days, ud[1::2])
    h0r, h1r, br_, pR = fit_eval(allidx[~odd], allidx[odd], "무작위 날짜")
    print("[검증 ③ 관측소 LOSO (n≥60인 관측소만)]")
    loso = []
    for sid in sorted(set(stn.tolist())):
        te = allidx[stn == sid]
        if len(te) < 60:
            continue
        loso.append(fit_eval(allidx[stn != sid], te, f"stn {sid} 제외→검증")[:2])
    if loso:
        l0 = float(np.mean([x[0] for x in loso])); l1 = float(np.mean([x[1] for x in loso]))
        print(f"  LOSO 평균: 현재 {l0:.2f} → 학습 {l1:.2f}")
    else:
        l0 = l1 = float("nan")
    hold0, hold1 = h0r, h1r; e_te = None
    verdict = "통과(승격 후보)" if (h1r < h0r - 0.2 and abs(br_) < 1.0 and (not loso or l1 < l0)) else "탈락"
    print(f"\n판정: {verdict}  (기준: 무작위날짜 검증 MAE 0.2↑ 개선 & |bias|<1 & LOSO 개선)")

    metrics = {"n": n, "mae_current": round(mae0, 3), "mae_fit": round(maeA, 3),
               "time_split": [round(h0t, 3), round(h1t, 3)], "random_day": [round(h0r, 3), round(h1r, 3), round(br_, 3)],
               "loso_station": [round(l0, 3), round(l1, 3)], "params_random": pR, "verdict": verdict}
    await conn.execute("INSERT INTO brain_version (kind, params, metrics, n_train, promoted, reason) VALUES ($1,$2,$3,$4,FALSE,$5)",
                       ("ground_lag_fixhc" if "--fix-hc" in sys.argv else "ground_lag"), json.dumps(pA), json.dumps(metrics, ensure_ascii=False), n, f"day2-1b {verdict}, 승격은 사람 확인")
    await conn.close()
    print("brain_version 에 kind='ground_lag' 기록 (promoted=false). 엔진 변경 없음.")


if __name__ == "__main__":
    asyncio.run(main())
