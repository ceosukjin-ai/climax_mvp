#!/usr/bin/env python3
"""노면 계수(hc_a·hc_b·f_stor) — 두 계절 실측으로 다시 맞추기 (2026-09-26).

재료 (둘 다 열화상 노면온도, 사람이 걷는 포장면)
  · 8월 부산 80곳   data/scs_master_80.csv        (11~15시, 볕/그늘 라벨 sun)
  · 3월 부산대 25곳 data/pnu_mar_ts_2026-09-26.csv (13~15시, 카메라 시계 +38~39분 보정)
    - Ts < Ta−3 인 2곳(point24·25)은 제외 — 하늘이 찍혔거나 짝 오류
    - 볕/그늘 라벨이 없어 흑구−기온 ≥ 4℃ 를 볕으로 본다(가정)
    - 'green' 유형은 노면이 잔디·흙일 수 있어 기본은 제외, 참고로 따로 보고
    - 3월 운량 기록 없음 → 맑음(0) 가정. 민감도로 0.3 도 같이 본다
τ(시간지연)·q_rel(야간방출)은 현재 엔진값 고정 — 두 자료 모두 한낮뿐이라 못 배운다(ASOS 몫).

검증: 계절 교차 — 8월로 맞춰 3월 채점 / 3월로 맞춰 8월 채점 / 둘 다로 맞춘 값.
엔진은 안 바꾼다(dry-run). 실행:
  docker run --rm -v $HOME/climax_mvp:/repo climax-backend:latest python3 /repo/scripts/fit_ground_2season.py
"""
import csv, math, os, sys, itertools
from dataclasses import replace
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/app")
from vpti_core import DEFAULT_CONFIG
from vpti_core.solar import estimate_solar, solar_lag_average
from vpti_core.mrt import estimate_ground_temp, sky_emissivity

ROOT = os.environ.get("CLIMAX_REPO", "/repo")
D = lambda f: os.path.join(ROOT, "data", f)
KST = timezone(timedelta(hours=9))
ALB, EMIS = 0.08, 0.94
BASE = DEFAULT_CONFIG.mrt
TAU = BASE.ground_lag_tau_h


def load_aug():
    cloud = {r["측정ID"]: float(r["cloud"]) for r in csv.DictReader(open(D("tier3_cloud_80.csv"), encoding="utf-8-sig"))}
    out = []
    for r in csv.DictReader(open(D("scs_master_80.csv"), encoding="utf-8-sig")):
        if not (r["SVF"].strip() or r["geo_SVF"].strip()) or not r["Ts"].strip():
            continue
        r["SVF"] = r["SVF"].strip() or r["geo_SVF"]          # 360 SVF 없으면 기하 SVF
        out.append(dict(id=r["측정ID"], set="8월", t=datetime.strptime(r["시각"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=KST),
                        lat=float(r["위도"]), lon=float(r["경도"]), ta=float(r["Ta"]), rh=float(r["RH"]), v=float(r["v"]),
                        svf=float(r["SVF"]), sun=r["sun"].strip() == "1", ts=float(r["Ts"]), cf=cloud.get(r["측정ID"], 0.0),
                        green=False))
    return out


def load_mar(cf_assume):
    meta = {r["측정ID"]: r for r in csv.DictReader(open(D("pnu_28_final.csv"), encoding="utf-8-sig"))}
    out = []
    for r in csv.DictReader(open(D("pnu_mar_ts_2026-09-26.csv"), encoding="utf-8-sig")):
        if not r["Ts"]:
            continue
        m = meta[r["측정ID"]]; ta = float(m["Ta"]); ts = float(r["Ts"])
        if ts < ta - 3:
            continue
        out.append(dict(id=r["측정ID"], set="3월", t=datetime.strptime(m["촬영일"] + " " + m["시각"][:5], "%Y-%m-%d %H:%M").replace(tzinfo=KST),
                        lat=float(m["위도"]), lon=float(m["경도"]), ta=ta, rh=float(m["RH"]), v=float(m["v"]),
                        svf=float(m["SVF"]), sun=(float(m["흑구"]) - ta) >= 4.0, ts=ts, cf=cf_assume,
                        green=(m["유형"] == "green")))
    return out


def prep(rows):
    for r in rows:
        r["sol"] = estimate_solar(r["lat"], r["lon"], r["t"], cloud_fraction=r["cf"])
        r["lag"] = solar_lag_average(r["lat"], r["lon"], r["t"], TAU, cloud_fraction=r["cf"]) if TAU > 0 else None
        r["eps"] = sky_emissivity(r["ta"], r["rh"], r["cf"])
    return rows


def ts_eng(r, cfg):
    return estimate_ground_temp(r["ta"], r["sol"], ALB, EMIS, r["svf"], 0.0, r["v"], r["eps"], config=cfg,
                                direct_shade=1.0 if r["sun"] else 0.0, solar_lag=r["lag"])


def score(rows, cfg):
    e = [ts_eng(r, cfg) - r["ts"] for r in rows]
    n = len(e)
    return (sum(map(abs, e)) / n, sum(e) / n, n) if n else (float("nan"), float("nan"), 0)


GRID = dict(hc_a=[6, 8, 10, 12, 14, 16, 20, 24, 28], hc_b=[2, 4, 6, 8],
            f_stor=[0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5])


def cfg_of(p):
    return replace(BASE, hc_a=float(p["hc_a"]), hc_b=float(p["hc_b"]), ground_storage_fraction=float(p["f_stor"]))


def best(sets):
    """sets: 맞출 자료 목록. 목적 = 자료별 (MAE + |bias|) 의 평균 — 한 계절이 다른 계절을 누르지 않게."""
    bestv = None
    for vals in itertools.product(*GRID.values()):
        p = dict(zip(GRID, vals)); cfg = cfg_of(p)
        s = 0.0
        for rows in sets:
            mae, b, _ = score(rows, cfg); s += mae + abs(b)
        s /= len(sets)
        if bestv is None or s < bestv[0]:
            bestv = (s, p)
    return bestv[1]


def line(name, p, aug, mar, marg):
    cfg = cfg_of(p) if p else BASE
    a = score(aug, cfg); m = score(mar, cfg); g = score(marg, cfg)
    tag = f"hc {cfg.hc_a:g}/{cfg.hc_b:g} f {cfg.ground_storage_fraction:g}"
    print(f"  {name:22} {tag:18} | 8월 MAE {a[0]:5.2f} bias {a[1]:+5.2f} (n{a[2]}) | 3월 MAE {m[0]:5.2f} bias {m[1]:+5.2f} (n{m[2]})"
          f" | 3월+잔디 MAE {g[0]:5.2f} bias {g[1]:+5.2f}")


def main():
    aug = prep(load_aug())
    for cf in (0.0, 0.3):
        mall = prep(load_mar(cf))
        mar = [r for r in mall if not r["green"]]
        print(f"\n=== 3월 운량 가정 {cf} · τ {TAU} · q_rel {BASE.ground_release_wm2} 고정 ===")
        print(f"  8월 {len(aug)}곳(볕 {sum(r['sun'] for r in aug)}) · 3월 포장 {len(mar)}곳(볕 {sum(r['sun'] for r in mar)}) · 3월 잔디포함 {len(mall)}곳")
        line("현재 엔진", None, aug, mar, mall)
        line("ASOS 새벽후보", dict(hc_a=28, hc_b=6, f_stor=0.1), aug, mar, mall)
        pa = best([aug]); line("8월로만 맞춤", pa, aug, mar, mall)
        pm = best([mar]); line("3월로만 맞춤", pm, aug, mar, mall)
        pj = best([aug, mar]); line("두 계절 함께", pj, aug, mar, mall)
        print("  → 교차검증: '8월로만'의 3월 성적, '3월로만'의 8월 성적이 현재보다 나쁘지 않아야 일반화된 것")


if __name__ == "__main__" and "--deep" not in sys.argv:
    main()


# ─── 구조 가설 시험: 땅속 온도로의 전도 손실 (2026-09-26) ─────────────────────────
# 위 결과에서 한 벌의 hc·f_stor 로 두 계절을 못 맞춘다(8월 맞추면 3월 +10℃ 과대).
# 가설: 저장항이 "흡수일사의 고정 비율"이라 계절을 모른다. 실제 땅은 **땅속 온도**로 열을 흘려보낸다
#       — 3월 땅속은 차갑고(≈월평균기온 10℃) 8월은 뜨겁다(≈26℃). 차가운 땅이 한낮 노면을 더 식힌다.
#   G = k_g·(Ts − T_deep),  T_deep ≈ 부산 월평균기온(평년, 근사치 — 3월 9.8, 8월 26.1).
# 엔진은 안 바꾸고 여기서 같은 수지식을 다시 풀어 본다(k_g=0 이면 엔진과 같아야 한다 — 먼저 확인).
import numpy as _np
from vpti_core.mrt import STEFAN_BOLTZMANN as _SB, KELVIN as _K
T_DEEP = {3: 9.8, 8: 26.1}


def ts_mine(r, hc_a, hc_b, f, kg, sun=None):
    sun = r["sun"] if sun is None else sun
    sol = r["sol"]; beta = math.radians(max(sol.solar_elevation_deg, 0.0)); svf = min(max(r["svf"], 0), 1)
    ds = 1.0 if sun else 0.0
    if r["lag"] is not None:
        s_down = max(r["lag"][0] * ds + r["lag"][1] * svf, 0.0)
    else:
        s_down = max(sol.dni * math.sin(beta) * ds + sol.dhi * svf, 0.0)
    avail = (1 - ALB) * s_down * (1 - f) + BASE.ground_release_wm2 * max(0.0, 1 - s_down / 200.0)
    ta = r["ta"]; ld = _SB * (ta + _K) ** 4 * (svf * r["eps"] + (1 - svf) * BASE.env_emissivity)
    h = hc_a + hc_b * r["v"]; td = T_DEEP[r["t"].month]
    ts = ta
    for _ in range(30):
        tk = ts + _K
        fx = avail + EMIS * (ld - _SB * tk ** 4) - h * (ts - ta) - kg * (ts - td)
        fp = -4 * EMIS * _SB * tk ** 3 - h - kg
        st = fx / fp; ts -= st
        if abs(st) < 1e-4: break
    if not sun and BASE.shade_ground_retention > 0:
        full = ts_mine(r, hc_a, hc_b, f, kg, sun=True)
        ts = ts + BASE.shade_ground_retention * (full - ts)
    return ts


def score2(rows, p):
    e = [ts_mine(r, p["hc_a"], p["hc_b"], p["f"], p["kg"]) - r["ts"] for r in rows]
    return sum(map(abs, e)) / len(e), sum(e) / len(e)


def main2():
    aug = prep(load_aug()); mar = [r for r in prep(load_mar(0.0)) if not r["green"]]
    chk = max(abs(ts_mine(r, BASE.hc_a, BASE.hc_b, BASE.ground_storage_fraction, 0) - ts_eng(r, BASE)) for r in aug + mar)
    print(f"\n=== 전도항 시험 (k_g=0 재현 오차 {chk:.3f}℃ — 0 이어야 식이 같다) ===")
    G = dict(hc_a=[8, 10, 12, 14, 16, 20], hc_b=[2, 4, 6], f=[0.05, 0.1, 0.15, 0.2, 0.25, 0.3], kg=[0, 2, 4, 6, 8, 10, 14, 20])
    res = []
    for v in itertools.product(*G.values()):
        p = dict(zip(G, v)); a = score2(aug, p); m = score2(mar, p)
        res.append(((a[0] + abs(a[1]) + m[0] + abs(m[1])) / 2, p, a, m))
    res.sort(key=lambda x: x[0])
    cur = dict(hc_a=BASE.hc_a, hc_b=BASE.hc_b, f=BASE.ground_storage_fraction, kg=0)
    a, m = score2(aug, cur), score2(mar, cur)
    print(f"  현재(k_g 0)            8월 MAE {a[0]:5.2f} bias {a[1]:+5.2f} | 3월 MAE {m[0]:5.2f} bias {m[1]:+5.2f}")
    for s, p, a, m in res[:6]:
        print(f"  hc {p['hc_a']}/{p['hc_b']} f {p['f']} k_g {p['kg']:2}  8월 MAE {a[0]:5.2f} bias {a[1]:+5.2f} | 3월 MAE {m[0]:5.2f} bias {m[1]:+5.2f}")
    # 교차: 8월만으로 k_g 포함 맞춤 → 3월 채점
    best_a = min(res, key=lambda x: x[2][0] + abs(x[2][1]))
    p = best_a[1]; m = best_a[3]
    print(f"  [교차] 8월로만 맞춤 hc {p['hc_a']}/{p['hc_b']} f {p['f']} k_g {p['kg']} → 3월 MAE {m[0]:.2f} bias {m[1]:+.2f}")


if __name__ == "__main__" and "--deep" in sys.argv:
    main2()
