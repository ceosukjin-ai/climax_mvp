#!/usr/bin/env python3
"""PET 잔차 AI 학습 — 물리 뼈대 위의 학습 보정 (2026-09-09).

입력: tier3_engine_output_80.csv (실측 80점 + 엔진 run_C Tmrt·PET). 타깃 = 실측 PET − 엔진 PET.
피처는 추론 시 쓸 수 있는 것만(실측 Ts 제외): 볕·SVF·GVI·기상·태양고도·엔진출력·NDVI.
검증: 근린 LOSO(처음 보는 근린 전이). 결과 → vpti_core/pet_residual.py 상수로 내장.

사용: python3 scripts/train_pet_residual.py data/tier3_engine_output_80.csv
"""
import csv, json, sys
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor

FEATS = ["볕", "tier3_svf", "tier3_gvi", "Ta", "RH", "v", "태양고도",
         "run_C_Tmrt", "run_C_PET", "ndvi30"]


def mae(a, b):
    return float(np.mean(np.abs(a - b)))


def main(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    f = lambda r, k: float(r[k])
    X = np.array([[f(r, k) for k in FEATS] for r in rows])
    y = np.array([f(r, "PET") for r in rows])
    ye = np.array([f(r, "run_C_PET") for r in rows])
    resid = y - ye
    groups = np.array([r["권역"] for r in rows])
    G = sorted(set(groups))
    print(f"n={len(rows)} 근린={G}")
    print(f"[baseline] 엔진 단독: MAE {mae(ye, y):.2f} bias {float(np.mean(ye - y)):+.2f}")

    def loso(fit_pred):
        pred = np.zeros(len(rows))
        for g in G:
            te = groups == g; tr = ~te
            pred[te] = ye[te] + fit_pred(X[tr], resid[tr], X[te])
        return pred

    def ridge_fp(alpha):
        def fp(Xtr, rtr, Xte):
            mu = Xtr.mean(0); sd = Xtr.std(0) + 1e-9
            m = Ridge(alpha=alpha).fit((Xtr - mu) / sd, rtr)
            return m.predict((Xte - mu) / sd)
        return fp

    def gbm_fp(d, it):
        def fp(Xtr, rtr, Xte):
            m = HistGradientBoostingRegressor(max_depth=d, max_iter=it, learning_rate=0.05,
                                              min_samples_leaf=5, random_state=0).fit(Xtr, rtr)
            return m.predict(Xte)
        return fp

    hot = y >= 41
    for name, fp in (("Ridge a=10", ridge_fp(10.0)), ("Ridge a=100", ridge_fp(100.0)),
                     ("GBM d2", gbm_fp(2, 60))):
        p = loso(fp)
        print(f"[LOSO] {name:11s} MAE {mae(p, y):.2f} bias {float(np.mean(p - y)):+.2f} "
              f"r {np.corrcoef(p, y)[0,1]:.3f} 극심 {int(((p >= 41) & hot).sum())}/{int(hot.sum())}")

    # 최종(전체 80) Ridge a=10 → 내장 상수
    mu = X.mean(0); sd = X.std(0) + 1e-9
    m = Ridge(alpha=10.0).fit((X - mu) / sd, resid)
    lo = X.min(0); hi = X.max(0); rng = hi - lo
    art = {"features": FEATS, "coef": [float(c) for c in m.coef_],
           "intercept": float(m.intercept_), "mean": [float(v) for v in mu],
           "std": [float(v) for v in sd],
           "bounds": {k: [float(lo[i] - 0.1 * rng[i]), float(hi[i] + 0.1 * rng[i])]
                      for i, k in enumerate(FEATS)},
           "trained_on": f"{len(rows)} field points Busan 2026-08 (Ridge a=10)", "alpha": 10.0}
    out = "pet_residual_ridge.json"
    json.dump(art, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"저장: {out}  (계수를 vpti_core/pet_residual.py 에 반영)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/tier3_engine_output_80_v4.csv")
