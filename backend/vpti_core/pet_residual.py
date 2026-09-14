"""
PET 잔차 AI — 물리 뼈대 위의 학습 보정 (2026-09-09).

물리 엔진(일사→MRT→PET)이 뼈대이고, AI는 물리가 놓치는 잔차(실측 PET − 엔진 PET)만 학습한다.
부산 실측 80지점(흑구·360°·열화상, 2026-08)으로 Ridge 회귀; 근린 LOSO(처음 보는 근린)에서
엔진단독 MAE 4.24 → 2.66, 극심(PET≥41) 탐지 84%→100%.
2026-09-14 재학습(현행 엔진: 벽 단파반사 ON·벽온도 스테이지 OFF): 물리 단독 MAE 5.23(bias +4.14)
→ 5피처 Ridge α=20, 근린 통째 제외(LONO) MAE 2.27, bias +0.06, r 0.68. 극심(PET≥41) 적중 63/63,
오경보 10/17(특이도 0.41, κ +0.52). 물리만으로는 특이도 0.35, κ +0.46.
재학습 이유: 물리가 바뀌어 엔진 PET 평균이 45.87 → 50.24 로 올라갔는데 잔차 계수는 옛 물리에 맞춰져 있었다.
그 상태의 배포 계수는 표본 내에서조차 MAE 3.56·bias +2.91(κ +0.17)로, 교차검증한 새 계수(2.27)보다 나빴다.
물리를 손볼 때마다 이 상수들을 같이 갱신해야 한다 — scripts/run_calib_refit.py 가 값을 찍어 준다.
물리가 좋아지자 10피처는 과적합(LOSO 3.55)이라 피처를 5개(볕·SVF·Ta·v·엔진PET)로 줄임. 순수 ML(원격피처만)이 LOSO R²<0으로
전이 실패한 것과 대비 — "AI는 물리 위에 얹혀야 다양한 공간에 일반화된다"의 실증.

일반화는 장소가 아니라 형태로: 피처에 좌표가 없어 안 가본 골목도 형태가 같으면 보정된다.
학습 형태에서 멀어질수록 확신도 c가 줄어 물리로 수렴(PET = 물리 + c·잔차); 터무니없는 외삽만 c=0.
잔차 계수 1위는 여전히 풍속(v, +2.07) — 엔진 물리 오차의 주범이 보행자 풍속(PWI)임을 시사.
2위는 엔진 PET 자체(-1.72): 엔진이 높게 낼수록 더 깎는다 = 물리에 남은 +4.14 의 계통 과대추정.
"""
from __future__ import annotations

FEATURES = ['볕', 'tier3_svf', 'Ta', 'v', 'run_C_PET']
_COEF = [-0.821710, 0.490910, 0.853994, 2.066249, -1.718506]
_INTERCEPT = -4.136625
_MEAN = [0.737500, 0.721714, 35.232500, 0.556250, 50.235000]
_STD = [0.439993, 0.145777, 1.492211, 0.442110, 5.445440]
TRAINED_ON = "80 field points Busan 2026-08, engine 2026-09-14 (wall shortwave reflection ON, wall temp stage OFF), Ridge a=20, 5 feats (LONO MAE 2.27, bias +0.06, r 0.68)"


def apply_pet_residual(pet_physics_c: float, feats: dict) -> tuple[float, bool, float]:
    """물리 PET → AI 잔차 보정 PET (확신도 가중, 2026-09-10).

    feats: FEATURES 키 전부(볕=direct_shade 0/1, tier3_svf, tier3_gvi, Ta, RH, v, 태양고도,
           run_C_Tmrt=엔진 Tmrt, run_C_PET=엔진 PET, ndvi30).
    반환: (보정 PET, 적용여부, 확신도 c∈[0,1]).  PET = 물리 + c·잔차.

    장소가 아니라 **형태**(SVF·H/W·그늘·기상…)로 일반화한다: 좌표는 피처에 없으므로 안 가본 골목도
    형태가 학습 범위 안이면 보정이 그대로 적용된다. 학습에서 본 형태에서 멀어질수록 c가 줄어 물리로
    수렴한다(끄는 게 아니라 줄임). 결측이거나 물리적으로 터무니없는 외삽(|z|>5)만 c=0.
      c = 1                     RMS-z ≤ 1.5  (학습 80점의 90%가 1.44 이내)
      c = (4 − RMS-z)/2.5       1.5 < RMS-z < 4
      c = 0                     RMS-z ≥ 4  또는 어느 피처든 |z| > 5
    """
    x = []
    for k in FEATURES:
        v = feats.get(k)
        if v is None:
            return pet_physics_c, False, 0.0
        x.append(float(v))
    z = [(xi - m) / s for xi, m, s in zip(x, _MEAN, _STD)]
    resid = _INTERCEPT + sum(c * zi for c, zi in zip(_COEF, z))
    dist = (sum(zi * zi for zi in z) / len(z)) ** 0.5
    if dist >= 4.0 or max(abs(zi) for zi in z) > 5.0:
        return pet_physics_c, False, 0.0
    conf = 1.0 if dist <= 1.5 else (4.0 - dist) / 2.5
    conf = max(0.0, min(1.0, conf))
    return pet_physics_c + conf * resid, conf > 0.0, conf
