#!/usr/bin/env python3
"""정본 숫자 한 벌 — 배포 코드로 돌린 근린 LOSO (2026-09-14).

왜 필요한가:
  지금 물리 -> 물리+AI 쌍이 문서마다 세 벌이다.
      논문 2.3절 회신(09-12)              3.28 -> 2.32
      tier3_loso_predictions.csv (Ridge)  3.50 -> 2.69
      2026-09-13 재적합(LONO)             3.50 -> 2.28
  어느 것이 정본인지 정해야 그림·방법 절·회신 문서가 같은 값을 말한다.
  이 스크립트가 그 정본을 만든다. 배포된 엔진 설정 그대로 물리 PET 를 계산하고,
  잔차층을 근린 통째 제외로 재적합하며, 배포 코드와 동일한 확신도 가중을 적용한다.

무엇을 재는가:
  물리        배포 설정의 엔진 PET (벽 단파반사 ON, 벽온도 스테이지 OFF)
  물리+AI     PET = 물리 + c * 잔차,  c 는 학습 분포로부터의 거리로 1 -> 0 감쇠
  검증        근린 하나를 통째로 빼고 나머지 네 근린으로 적합 (LONO)
              지점 하나만 빼면 같은 근린·같은 분의 이웃이 학습에 남아 낙관적이다.

주의 — 이 표는 볕/그늘을 **실측으로 준** 조건이다. 실서비스는 엔진이 스스로 판정하며
그 일치도는 kappa 0.06 에 불과하다(2026-09-13). 따라서 운영 성능은 이 값보다 나쁘다.
논문에 쓸 때 이 단서를 함께 적을 것.

  docker cp scripts/run_loso_official.py climax-api:/tmp/
  docker cp data/tier3_engine_output_80_v6.csv climax-api:/tmp/
  docker exec -i climax-api python3 /tmp/run_loso_official.py
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
from vpti_core.solar import estimate_solar                # noqa: E402
from vpti_core.vsi import ViewSegmentation                # noqa: E402
from vpti_core.smti import MaterialFraction               # noqa: E402
from vpti_core.vpti import WeatherContext, compute_vpti_thermal   # noqa: E402
from vpti_core.comfort import compute_pet                 # noqa: E402
from vpti_core import pet_residual as PR                  # noqa: E402
from app.config import get_settings                       # noqa: E402
from app.services.geo import dominant_wall_material       # noqa: E402

IN = "/tmp/tier3_engine_output_80_v6.csv"
OUT = "/tmp/loso_official.json"
MATS = [MaterialFraction(material="asphalt", fraction=0.7),
        MaterialFraction(material="concrete", fraction=0.3)]
ALPHA = 20.0                       # 배포 잔차층과 같은 능형 계수
THRESH = 41.0                      # 극심 열스트레스


def views(svf: float, gvi: float):
    b = max(0.0, min(1.0 - gvi, 1.0 - svf))
    sky_h = max(0.0, min(0.5, svf / 2.0))
    vs = [ViewSegmentation(direction="up", sky_ratio=max(0.0, min(1.0, svf)),
                           vegetation_ratio=0.0, building_ratio=0.0)]
    vs += [ViewSegmentation(direction=d, sky_ratio=sky_h, vegetation_ratio=gvi,
                            building_ratio=b) for d in ("front", "back", "left", "right")]
    return vs


def ridge_fit(X, y, alpha=ALPHA):
    """표준화 후 능형 회귀. 절편은 벌점에서 뺀다. (mean, std, coef, intercept) 반환."""
    m, s = X.mean(0), X.std(0)
    s = np.where(s < 1e-12, 1.0, s)
    Z = (X - m) / s
    Z1 = np.column_stack([np.ones(len(Z)), Z])
    P = np.eye(Z1.shape[1]); P[0, 0] = 0.0
    b = np.linalg.solve(Z1.T @ Z1 + alpha * P, Z1.T @ y)
    return m, s, b[1:], b[0]


def apply_with_conf(x, m, s, coef, intercept):
    """배포 코드(pet_residual.apply_pet_residual)와 동일한 확신도 규칙."""
    z = (x - m) / s
    resid = intercept + float(np.dot(coef, z))
    dist = float(np.sqrt(np.mean(z * z)))
    if dist >= 4.0 or float(np.max(np.abs(z))) > 5.0:
        return 0.0, 0.0
    conf = 1.0 if dist <= 1.5 else (4.0 - dist) / 2.5
    conf = max(0.0, min(1.0, conf))
    return conf * resid, conf


def score(pred, truth):
    e = [p - t for p, t in zip(pred, truth)]
    n = len(e)
    mae = sum(map(abs, e)) / n
    bias = sum(e) / n
    mp, mt = statistics.mean(pred), statistics.mean(truth)
    sp = math.sqrt(sum((p - mp) ** 2 for p in pred) / n)
    st = math.sqrt(sum((t - mt) ** 2 for t in truth) / n)
    r = (sum((pred[i] - mp) * (truth[i] - mt) for i in range(n)) / n / (sp * st)
         if sp > 0 and st > 0 else float("nan"))
    hot = [i for i in range(n) if truth[i] >= THRESH]
    hit = sum(1 for i in hot if pred[i] >= THRESH)
    return dict(n=n, mae=round(mae, 3), bias=round(bias, 3), r=round(r, 3),
                hot=len(hot), hit=hit)


def line(label, d):
    print(f"  {label:34} MAE {d['mae']:5.2f}   bias {d['bias']:+5.2f}   "
          f"r {d['r']:5.2f}   극심 {d['hit']}/{d['hot']}")


async def main() -> None:
    st = get_settings()
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    truth = [float(r["PET"]) for r in rows]
    grp = [r["권역"] for r in rows]
    sun = [1.0 if str(r["볕"]).strip() == "1" else 0.0 for r in rows]

    print(f"\n입력 {len(rows)}행  {IN}")
    print(f"배포 설정  GEO_WALL_REFLECT={st.geo_wall_reflect}  "
          f"GEO_WALL_STAGE={st.geo_wall_stage}")
    print(f"           GEO_TREE_SHADE(KR)={getattr(st, 'geo_tree_shade', 'n/a')}  "
          f"JP={getattr(st, 'geo_tree_shade_jp', 'n/a')}")
    print(f"잔차층 배포 계수  alpha=20  {PR.FEATURES}")
    print(f"           {PR.TRAINED_ON}\n")

    # --- 1. 물리 PET (배포 설정 그대로)
    cache: dict[tuple, dict] = {}
    phys, Xrows = [], []
    for i, r in enumerate(rows):
        lat, lon = float(r["위도"]), float(r["경도"])
        k = (round(lat, 5), round(lon, 5))
        if k not in cache:
            cache[k] = await dominant_wall_material(lat, lon)
        alb = cache[k]["albedo"] if st.geo_wall_reflect else None
        when = datetime.strptime(r["시각"], "%Y-%m-%d %H:%M:%S")     # naive = KST
        ta, rh, v = float(r["Ta"]), float(r["RH"]), float(r["v"])
        svf, gvi = float(r["tier3_svf"]), float(r["tier3_gvi"])
        wc = WeatherContext(temperature_c=ta, humidity_pct=rh, wind_speed_ms=v,
                            wind_direction_deg=0.0)
        res = compute_vpti_thermal(views_5=views(svf, gvi), materials=MATS, weather=wc,
                                   road_axis_deg=0.0, lat=lat, lon=lon, when=when,
                                   direct_shade=sun[i],      # 실측 볕/그늘
                                   wall_temp_c=None,         # 벽온도 스테이지 OFF
                                   wall_albedo=alb,
                                   wind_is_pedestrian=True)
        tg = float(res.mrt.tmrt_globe)                        # 흑구 기준 — 실측과 같은 정의
        pet = float(compute_pet(tdb=ta, tr=tg, v=res.pedestrian_wind_ms, rh=rh,
                                season=res.season,
                                config=DEFAULT_CONFIG.comfort).value)
        phys.append(pet)
        Xrows.append([sun[i], svf, ta, v, pet])              # 배포 FEATURES 순서

    X = np.array(Xrows, dtype=float)
    y = np.array([truth[i] - phys[i] for i in range(len(rows))], dtype=float)

    # --- 2. 근린 통째 제외 재적합 (LONO) — 정본
    lono = [0.0] * len(rows)
    confs = [0.0] * len(rows)
    for u in sorted(set(grp)):
        te = [i for i in range(len(rows)) if grp[i] == u]
        tr = [i for i in range(len(rows)) if grp[i] != u]
        m, s, coef, b0 = ridge_fit(X[tr], y[tr])
        for i in te:
            d, c = apply_with_conf(X[i], m, s, coef, b0)
            lono[i] = phys[i] + d
            confs[i] = c

    # --- 3. 배포 계수를 그대로 쓴 경우 (표본 내 — 참고용)
    ship = []
    for i, r in enumerate(rows):
        f = dict(zip(PR.FEATURES, Xrows[i]))
        p, _applied, _c = PR.apply_pet_residual(phys[i], f)
        ship.append(p)

    s_phys, s_lono, s_ship = score(phys, truth), score(lono, truth), score(ship, truth)
    print("=== 정본 (근린 통째 제외 재적합) ===")
    line("물리 단독", s_phys)
    line("물리 + 잔차 (LONO)", s_lono)
    print()
    print("=== 참고 ===")
    line("물리 + 잔차 (배포 계수, 표본 내)", s_ship)
    print(f"  확신도 c  평균 {statistics.mean(confs):.2f}  최소 {min(confs):.2f}  "
          f"c=0 인 지점 {sum(1 for c in confs if c == 0)}개")

    print("\n=== 근린별 (LONO) ===")
    per = {}
    for u in sorted(set(grp)):
        idx = [i for i in range(len(rows)) if grp[i] == u]
        d = score([lono[i] for i in idx], [truth[i] for i in idx])
        per[u] = d
        line(f"  {u}", d)

    out = dict(generated="2026-09-14", input=IN, n=len(rows),
               settings=dict(wall_reflect=st.geo_wall_reflect,
                             wall_stage=st.geo_wall_stage),
               physics=s_phys, physics_plus_ai_lono=s_lono,
               physics_plus_ai_shipped_insample=s_ship,
               per_neighbourhood_lono={k: v for k, v in per.items()},
               note=("볕/그늘을 실측으로 준 조건. 실서비스는 엔진 판정을 쓰며 "
                     "일치도 kappa 0.06 이므로 운영 성능은 이보다 나쁘다."))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n저장: {OUT}")
    # 지점별 예측 (티어 사다리 (f) 패널용) — 2026-09-19
    OUT_SITES = OUT.replace(".json", "_sites.csv")
    with open(OUT_SITES, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(["측정ID", "권역", "볕", "실측PET", "물리_PET", "물리+AI_LONO_PET", "물리+AI_배포계수_PET", "확신도"])
        for i, r in enumerate(rows):
            w.writerow([r["측정ID"], r["권역"], int(sun[i]), truth[i], round(phys[i], 2), round(lono[i], 2), round(ship[i], 2), round(confs[i], 3)])
    print(f"저장: {OUT_SITES}")

    print("\n=== 논문·그림·문서에 그대로 쓸 문장 ===")
    print(f"  물리 단독 MAE {s_phys['mae']:.2f} °C, 물리+잔차 {s_lono['mae']:.2f} °C "
          f"(근린 통째 제외 교차검증, n={s_lono['n']}),")
    print(f"  극심 열스트레스(PET >= 41 °C) {s_lono['hit']}/{s_lono['hot']} 탐지, "
          f"bias {s_lono['bias']:+.2f} °C, r {s_lono['r']:.2f}.")


if __name__ == "__main__":
    asyncio.run(main())
