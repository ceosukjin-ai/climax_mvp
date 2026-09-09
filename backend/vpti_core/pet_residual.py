"""
PET 잔차 AI — 물리 뼈대 위의 학습 보정 (2026-09-09).

물리 엔진(일사→MRT→PET)이 뼈대이고, AI는 물리가 놓치는 잔차(실측 PET − 엔진 PET)만 학습한다.
부산 실측 80지점(흑구·360°·열화상, 2026-08)으로 Ridge 회귀; 근린 LOSO(처음 보는 근린)에서
엔진단독 MAE 4.24 → 2.66, 극심(PET≥41) 탐지 84%→100%. 순수 ML(원격피처만)이 LOSO R²<0으로
전이 실패한 것과 대비 — "AI는 물리 위에 얹혀야 다양한 공간에 일반화된다"의 실증.

특허 설계 그대로: 입력이 학습 분포 밖이면 보정을 끄고 물리 PET로 폴백(R_AI=1 상당).
잔차 계수 1위는 풍속(v) — 엔진 물리 오차의 주범이 보행자 풍속(PWI)임을 시사.
"""
from __future__ import annotations

FEATURES = ['볕', 'tier3_svf', 'tier3_gvi', 'Ta', 'RH', 'v', '태양고도', 'run_C_Tmrt', 'run_C_PET', 'ndvi30']
_COEF = [0.03854635625078417, 0.5240287875278188, 0.3344906746538077, 1.1331114736542576, -0.2357673507684243, 1.9936776533854863, 0.07925481177568829, 0.10918662878963023, -1.4287412260744894, -0.40036178366561986]
_INTERCEPT = 1.6799999999999895
_MEAN = [0.7375, 0.7217137499999997, 0.03488375, 35.23249999999999, 49.015, 0.5562499999999999, 62.230000000000004, 52.950625, 44.418375, 0.14764253344763992]
_STD = [0.43999289866995084, 0.14577655153518554, 0.04986187381819145, 1.4922110283014343, 5.152186430080376, 0.4421096452060499, 5.669929453823907, 4.469153540471988, 3.8962207480027944, 0.12361080517765517]
# 학습 분포 경계(min/max ±10%) — 밖이면 폴백
_BOUNDS = {"볕": [-0.1, 1.1], "tier3_svf": [0.22188999999999998, 1.03681], "tier3_gvi": [-0.030109999999999998, 0.33121], "Ta": [31.33, 39.370000000000005], "RH": [38.46, 64.14], "v": [-0.22000000000000003, 2.4200000000000004], "태양고도": [43.89, 69.21], "run_C_Tmrt": [42.055, 59.154999999999994], "run_C_PET": [33.909, 52.760999999999996], "ndvi30": [-0.05181709475443823, 0.7594938183947761]}
TRAINED_ON = "80 field points Busan 2026-08, engine v4 (pedestrian wind + shade ground retention 0.74) (LOSO MAE 2.69)"


def apply_pet_residual(pet_physics_c: float, feats: dict) -> tuple[float, bool, float]:
    """물리 PET → AI 잔차 보정 PET.

    feats: FEATURES 키 전부(볕=direct_shade 0/1, tier3_svf, tier3_gvi, Ta, RH, v, 태양고도,
           run_C_Tmrt=엔진 Tmrt, run_C_PET=엔진 PET, ndvi30).
    반환: (보정 PET, 적용여부, 신뢰도[0,1]). 분포 밖/결측이면 (물리 PET, False, 0.0) 폴백.
    """
    x = []
    n_out = 0
    for k in FEATURES:
        v = feats.get(k)
        if v is None:
            return pet_physics_c, False, 0.0
        v = float(v)
        lo, hi = _BOUNDS[k]
        if v < lo or v > hi:
            n_out += 1
        x.append(v)
    if n_out > 0:                                   # 학습 분포 밖 → 물리 폴백(특허 게이트)
        return pet_physics_c, False, 0.0
    z = [(xi - m) / s for xi, m, s in zip(x, _MEAN, _STD)]
    resid = _INTERCEPT + sum(c * zi for c, zi in zip(_COEF, z))
    # 신뢰도: 표준화 거리 기반(분포 중심일수록 1)
    dist = (sum(zi * zi for zi in z) / len(z)) ** 0.5
    conf = max(0.0, min(1.0, 1.0 - dist / 3.0))
    return pet_physics_c + resid, True, conf
