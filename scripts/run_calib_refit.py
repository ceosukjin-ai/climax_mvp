#!/usr/bin/env python3
"""벽반사 A/B + 극심 혼동행렬 + 잔차층 재적합 (2026-09-14).

run_loso_official.py 가 정본 숫자를 냈고, 그 안에서 문제 세 개가 같이 나왔다.
이 스크립트가 그 셋을 한 번에 정리한다.

  1. 벽 단파반사를 켠 뒤 물리 단독 MAE 가 3.50 -> 5.23, bias +4.14 로 나빠졌다.
     반사 ON/OFF 를 같은 표에 올려서, 반사가 정말 물리를 나쁘게 만든 것인지
     아니면 다른 변화와 겹친 것인지 가른다.

  2. 80점 중 63점이 실측 PET >= 41 이다. 물리는 bias +4.14 로 전부 띄우니
     63/63 이 공짜로 나온다. 재현율만 찍지 말고 오경보(17점 중 몇 개)까지 센다.

  3. 배포된 잔차 계수가 표본 내인데도 MAE 3.56, bias +2.91 이다. 교차검증
     2.27 보다 나쁘다 -- 옛 물리(engine v7)에 맞춘 계수를 새 물리에 쓰고 있다.
     현재 물리 기준으로 전체 적합해서 새 계수를 찍는다. 붙여넣을 형태로 낸다.

주의 -- 볕/그늘을 실측으로 준 조건이다. 실서비스는 엔진이 스스로 판정하며
그 일치도는 kappa 0.06 이다. 운영 성능은 이 표보다 나쁘다.

  docker cp scripts/run_calib_refit.py climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/run_calib_refit.py
"""
from __future__ import annotations
import asyncio
import csv
import json
import math
import statistics
import sys
from datetime import datetime

sys.path.insert(0, "/app")

import numpy as np                                        # noqa: E402
from vpti_core import DEFAULT_CONFIG                      # noqa: E402
from vpti_core.vsi import ViewSegmentation               # noqa: E402
from vpti_core.smti import MaterialFraction              # noqa: E402
from vpti_core.vpti import WeatherContext, compute_vpti_thermal   # noqa: E402
from vpti_core.comfort import compute_pet                # noqa: E402
from vpti_core import pet_residual as PR                 # noqa: E402
from app.config import get_settings                      # noqa: E402
from app.services.geo import dominant_wall_material      # noqa: E402

IN = "/tmp/tier3_engine_output_80_v6.csv"
OUT = "/tmp/calib_refit.json"
MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]
ALPHA = 20.0
THRESH = 41.0


def views(svf: float, gvi: float):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi,
                            building_ratio=b) for d in ("front", "back", "left", "right")]
    return vs


def ridge_fit(X, y, alpha=ALPHA):
    m, s = X.mean(0), X.std(0)
    s = np.where(s < 1e-12, 1.0, s)
    Z = (X - m) / s
    Z1 = np.column_stack([np.ones(len(Z)), Z])
    P = np.eye(Z1.shape[1]); P[0, 0] = 0.0
    b = np.linalg.solve(Z1.T @ Z1 + alpha * P, Z1.T @ y)
    return m, s, b[1:], b[0]


def apply_with_conf(x, m, s, coef, intercept):
    z = (x - m) / s
    resid = intercept + float(np.dot(coef, z))
    dist = float(np.sqrt(np.mean(z * z)))
    if dist >= 4.0 or float(np.max(np.abs(z))) > 5.0:
        return 0.0, 0.0
    conf = 1.0 if dist <= 1.5 else (4.0 - dist) / 2.5
    conf = max(0.0, min(1.0, conf))
    return conf * resid, conf


def confusion(pred, truth):
    """극심(>=41) 을 양성으로 본 혼동행렬."""
    tp = sum(1 for p, t in zip(pred, truth) if p >= THRESH and t >= THRESH)
    fp = sum(1 for p, t in zip(pred, truth) if p >= THRESH and t < THRESH)
    fn = sum(1 for p, t in zip(pred, truth) if p < THRESH and t >= THRESH)
    tn = sum(1 for p, t in zip(pred, truth) if p < THRESH and t < THRESH)
    n = tp + fp + fn + tn
    po = (tp + tn) / n
    pe = ((tp + fn) * (tp + fp) + (fp + tn) * (fn + tn)) / (n * n)
    kap = (po - pe) / (1 - pe) if pe < 1 else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    spec = tn / (tn + fp) if tn + fp else float("nan")
    prec = tp / (tp + fp) if tp + fp else float("nan")
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, recall=round(rec, 3),
                specificity=round(spec, 3), precision=round(prec, 3),
                accuracy=round(po, 3), kappa=round(kap, 3))


def score(pred, truth):
    e = [p - t for p, t in zip(pred, truth)]
    n = len(e)
    mae = sum(map(abs, e)) / n
    bias = sum(e) / n
    rmse = math.sqrt(sum(x * x for x in e) / n)
    mp, mt = statistics.mean(pred), statistics.mean(truth)
    sp = math.sqrt(sum((p - mp) ** 2 for p in pred) / n)
    st = math.sqrt(sum((t - mt) ** 2 for t in truth) / n)
    r = (sum((pred[i] - mp) * (truth[i] - mt) for i in range(n)) / n / (sp * st)
         if sp > 0 and st > 0 else float("nan"))
    d = dict(n=n, mae=round(mae, 3), bias=round(bias, 3), rmse=round(rmse, 3),
             r=round(r, 3))
    d.update(confusion(pred, truth))
    return d


def line(label, d):
    print(f"  {label:32} MAE {d['mae']:5.2f}  bias {d['bias']:+5.2f}  "
          f"r {d['r']:5.2f}   극심 적중 {d['tp']:2d}/{d['tp']+d['fn']:2d}  "
          f"오경보 {d['fp']:2d}/{d['fp']+d['tn']:2d}  kappa {d['kappa']:+.2f}")


async def physics(rows, sun, albs, use_wall: bool):
    out, Xrows = [], []
    for i, r in enumerate(rows):
        lat, lon = float(r["위도"]), float(r["경도"])
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")   # naive = KST
        ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"])
        svf, gvi = float(r["tier3_svf"]), float(r["tier3_gvi"])
        wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v,
                            wind_direction_deg=0.0)
        res = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc,
                                   road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                                   direct_shade=sun[i], wall_temp_c=None,
                                   wall_albedo=(albs[i] if use_wall else None),
                                   wind_is_pedestrian=True)
        tg = float(res.mrt.tmrt_globe)
        pet = float(compute_pet(tdb=ta, tr=tg, v=res.pedestrian_wind_ms, rh=rh,
                                season=res.season,
                                config=DEFAULT_CONFIG.comfort).value)
        out.append(pet)
        Xrows.append([sun[i], svf, ta, v, pet])
    return out, np.array(Xrows, dtype=float)


def lono(X, y, phys, grp):
    pred = [0.0] * len(phys)
    confs = [0.0] * len(phys)
    for u in sorted(set(grp)):
        te = [i for i in range(len(phys)) if grp[i] == u]
        tr = [i for i in range(len(phys)) if grp[i] != u]
        m, s, coef, b0 = ridge_fit(X[tr], y[tr])
        for i in te:
            d, c = apply_with_conf(X[i], m, s, coef, b0)
            pred[i] = phys[i] + d
            confs[i] = c
    return pred, confs


async def main() -> None:
    st = get_settings()
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    truth = [float(r["PET"]) for r in rows]
    grp = [r["권역"] for r in rows]
    sun = [1.0 if str(r["볕"]).strip() == "1" else 0.0 for r in rows]

    print(f"\n입력 {len(rows)}행   실측 극심(PET>={THRESH:.0f}) "
          f"{sum(1 for t in truth if t >= THRESH)} / 비극심 "
          f"{sum(1 for t in truth if t < THRESH)}")
    print(f"배포 설정  GEO_WALL_REFLECT={st.geo_wall_reflect}  "
          f"GEO_WALL_STAGE={st.geo_wall_stage}\n")

    cache: dict[tuple, dict] = {}
    albs = []
    for r in rows:
        k = (round(float(r["위도"]), 5), round(float(r["경도"]), 5))
        if k not in cache:
            cache[k] = await dominant_wall_material(k[0], k[1])
        albs.append(cache[k]["albedo"])
    print(f"외벽 알베도  평균 {statistics.mean(albs):.3f}  "
          f"범위 {min(albs):.3f}~{max(albs):.3f}  좌표 {len(cache)}개\n")

    # --- 1. 벽반사 A/B
    ph_on, X_on = await physics(rows, sun, albs, True)
    ph_off, X_off = await physics(rows, sun, albs, False)
    y_on = np.array([truth[i] - ph_on[i] for i in range(len(rows))])
    y_off = np.array([truth[i] - ph_off[i] for i in range(len(rows))])
    lo_on, c_on = lono(X_on, y_on, ph_on, grp)
    lo_off, c_off = lono(X_off, y_off, ph_off, grp)

    print("=== 1. 벽 단파반사 A/B ===")
    line("물리      반사 ON  (배포)", score(ph_on, truth))
    line("물리      반사 OFF", score(ph_off, truth))
    line("물리+잔차 반사 ON  (LONO)", score(lo_on, truth))
    line("물리+잔차 반사 OFF (LONO)", score(lo_off, truth))
    d_ph = score(ph_on, truth)["mae"] - score(ph_off, truth)["mae"]
    d_ai = score(lo_on, truth)["mae"] - score(lo_off, truth)["mae"]
    print()
    print(f"  물리 차이 {d_ph:+.2f} °C,  물리+잔차 차이 {d_ai:+.2f} °C")
    if d_ph > 0.2 and d_ai > 0.05:
        print("  -> 반사가 양쪽 다 나쁘게 한다. 반사 계수/벽온도 가정을 고쳐야 한다.")
    elif d_ph > 0.2:
        print("  -> 반사는 물리만 나쁘게 하고 최종은 같다. 잔차층이 가려주고 있을 뿐이다.")
        print("     논문에서 '물리 충실도 향상'으로 쓸 수 없다. 계수를 교정하든지 끄든지 택해야 한다.")
    else:
        print("  -> 반사 때문이 아니다. 물리 악화의 원인이 다른 데 있다.")

    # --- 2. 극심 판정 혼동행렬
    print("\n=== 2. 극심 열스트레스 판정 (PET >= 41) ===")
    for lbl, pr in (("물리 (반사 ON)", ph_on), ("물리+잔차 (LONO)", lo_on)):
        c = confusion(pr, truth)
        print(f"  {lbl:20} 적중 {c['tp']}/{c['tp']+c['fn']}  "
              f"오경보 {c['fp']}/{c['fp']+c['tn']}  "
              f"정밀도 {c['precision']:.2f}  특이도 {c['specificity']:.2f}  "
              f"kappa {c['kappa']:+.2f}")
    print("  (물리의 100 % 재현율은 bias 가 전부 띄운 결과다. 특이도를 같이 쓸 것.)")

    # --- 3. 현재 물리 기준 전체 재적합 -> 새 배포 계수
    print("\n=== 3. 잔차층 재적합 (현재 물리, 전체 80점) ===")
    ship = [PR.apply_pet_residual(ph_on[i], dict(zip(PR.FEATURES, X_on[i])))[0]
            for i in range(len(rows))]
    line("현행 배포 계수 (표본 내)", score(ship, truth))
    m, s, coef, b0 = ridge_fit(X_on, y_on)
    new = [ph_on[i] + apply_with_conf(X_on[i], m, s, coef, b0)[0]
           for i in range(len(rows))]
    line("재적합 계수 (표본 내)", score(new, truth))
    line("재적합 계수 (LONO 검증)", score(lo_on, truth))
    print(f"  확신도 c  평균 {statistics.mean(c_on):.2f}  최소 {min(c_on):.2f}  "
          f"c=0 {sum(1 for c in c_on if c == 0)}개")

    print("\n--- pet_residual.py 에 넣을 값 ---")
    print(f"FEATURES = {list(PR.FEATURES)}")
    print("MEAN      = [" + ", ".join(f"{v:.6f}" for v in m) + "]")
    print("STD       = [" + ", ".join(f"{v:.6f}" for v in s) + "]")
    print("COEF      = [" + ", ".join(f"{v:.6f}" for v in coef) + "]")
    print(f"INTERCEPT = {b0:.6f}")
    print(f"ALPHA     = {ALPHA}")
    print('TRAINED_ON = "80 field points Busan 2026-08, engine 2026-09-14 '
          '(wall shortwave reflection ON, wall temp stage OFF), Ridge a=20, '
          f'5 feats (LONO MAE {score(lo_on, truth)["mae"]:.2f})"')
    print("\n  기여도 (표준화 계수, 큰 것부터):")
    for nm, cv in sorted(zip(PR.FEATURES, coef), key=lambda t: -abs(t[1])):
        print(f"    {nm:14} {cv:+7.3f}")

    json.dump(dict(generated="2026-09-14", n=len(rows),
                   wall_on=dict(physics=score(ph_on, truth),
                                lono=score(lo_on, truth)),
                   wall_off=dict(physics=score(ph_off, truth),
                                 lono=score(lo_off, truth)),
                   shipped_insample=score(ship, truth),
                   refit_insample=score(new, truth),
                   refit=dict(features=list(PR.FEATURES), mean=list(map(float, m)),
                              std=list(map(float, s)), coef=list(map(float, coef)),
                              intercept=float(b0), alpha=ALPHA)),
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
