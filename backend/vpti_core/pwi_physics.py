"""
PWI 물리 방식 — 도시 형태밀도 기반 보행자 풍속 (거칠기-로그분포-캐노피 감쇠).

지금 앱(app.core.pwi)은 높이 프로파일 지수 α=0.30 을 도시 전역 고정으로 쓴다.
즉 건물이 빽빽하든 뻥 뚫렸든 10m→1.5m 감쇠가 같고, 밀도는 SVF·BVI로 약하게만
들어간다. 이 모듈은 그 고정 지수를 **건물 밀도(공기역학 거칠기)** 로 대체한다.

── 물리 근거(문헌) ─────────────────────────────────────────
1) Macdonald et al. (1998, Atmos. Environ. 32) — 형태밀도 → 거칠기
     변위고도  d/H  = 1 + A^(−λp)·(λp − 1),                 A = 4.43 (엇배열)
     거칠기길이 z0/H = (1 − d/H)·exp{ −[½·β·Cd/κ²·(1 − d/H)·λf]^(−½) }
                      β = 1.0(보정), Cd = 1.2(항력), κ = 0.40(von Kármán)
2) 로그 풍속분포 — 개활지 관측(10m)을 지형무관 공통상공(≈100m)으로 올린 뒤
     도시 거칠기로 캐노피 상단(H)까지 내린다.
3) Cionco (1965) 지수 캐노피 감쇠 — 캐노피 내부는
     U(z) = U(H)·exp[a·(z/H − 1)],  a: 정면밀도 λf 클수록 커짐.

입력 형태밀도:
  λp = 평면적밀도 ≈ 건폐율 (footprint/부지)     ← V-World footprint / 건축물대장 bcRat
  λf = 정면적밀도 ≈ (건물 정면 × 높이)/부지        ← 건축물대장 면적·높이 기하 or BVI 대리
  H  = 건물(캐노피) 높이 [m]                       ← 건축물대장 heit or 층수×층고

⚠️ 모든 계수(A·Cd·β·Z0_OPEN·Z_BLEND·Cionco a)와 λf 근사는 UNCONFIRMED 문헌/기하
   보수값 — 자작 풍속계 실측으로 교정 예정. 절대 m/s 참고, 밀도 상대패턴 신뢰.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ── 문헌 상수 (⚠️UNCONFIRMED, 교정 대상) ──
KAPPA = 0.40          # von Kármán
A_MAC = 4.43          # Macdonald 엇배열 상수
CD = 1.2              # 항력계수
BETA = 1.0            # Macdonald 보정
Z0_OPEN = 0.03        # 개활지(AWS 노출) 거칠기길이 [m]
Z_REF = 10.0          # AWS 관측고 [m]
Z_BLEND = 100.0       # 지형무관 공통 상공 [m]
Z_PED = 1.5           # 보행자 높이 [m]
CIONCO_A0 = 1.0       # 캐노피 감쇠 기저
CIONCO_A1 = 3.0       # 캐노피 감쇠 λf 민감도 (a = A0 + A1·λf)

# 형태값 결측 시 대표 보수값 (도시 일반)
DEFAULT_H_M = 15.0
DEFAULT_LAMBDA_P = 0.35
DEFAULT_LAMBDA_F = 0.30


@dataclass(frozen=True, slots=True)
class RoughnessResult:
    pedestrian_wind_speed_ms: float  # u_p (1.5m)
    z0_urban_m: float                # 도시 거칠기길이
    disp_height_m: float             # 변위고도 d
    u_canopy_top_ms: float           # 캐노피 상단(H) 풍속
    canopy_atten_a: float            # Cionco a
    lambda_p: float
    lambda_f: float
    height_m: float
    profile_exponent_equiv: float    # 등가 지수 α (지금 앱 0.30 고정과 비교용)

    def as_dict(self) -> dict:
        return {
            "pedestrian_wind_speed_ms": round(self.pedestrian_wind_speed_ms, 3),
            "z0_urban_m": round(self.z0_urban_m, 3),
            "disp_height_m": round(self.disp_height_m, 2),
            "u_canopy_top_ms": round(self.u_canopy_top_ms, 3),
            "canopy_atten_a": round(self.canopy_atten_a, 3),
            "lambda_p": round(self.lambda_p, 3),
            "lambda_f": round(self.lambda_f, 3),
            "height_m": round(self.height_m, 1),
            "profile_exponent_equiv": round(self.profile_exponent_equiv, 3),
        }


def macdonald_z0_d(lambda_p: float, lambda_f: float, height_m: float) -> tuple[float, float]:
    """Macdonald 1998 — 형태밀도 → (z0[m], d[m])."""
    lp = min(max(lambda_p, 0.0), 0.9)
    lf = max(lambda_f, 1e-3)
    d_over_H = min(max(1.0 + A_MAC ** (-lp) * (lp - 1.0), 0.0), 0.95)
    inner = 0.5 * BETA * CD / KAPPA**2 * (1.0 - d_over_H) * lf
    z0_over_H = (1.0 - d_over_H) * math.exp(-(inner) ** -0.5) if inner > 0 else 1e-3
    return max(z0_over_H * height_m, 1e-3), d_over_H * height_m


def pedestrian_wind_physics(
    wind_speed_10m: float,
    *,
    lambda_p: float | None = None,
    lambda_f: float | None = None,
    height_m: float | None = None,
) -> RoughnessResult:
    """개활지 10m 풍속 → 물리(거칠기) 보행자 풍속.

    형태값이 None이면 도시 일반 보수값으로 대체(결측 안전).
    """
    lp = DEFAULT_LAMBDA_P if lambda_p is None else lambda_p
    lf = DEFAULT_LAMBDA_F if lambda_f is None else lambda_f
    H = DEFAULT_H_M if (height_m is None or height_m < 2.0) else height_m

    z0u, d = macdonald_z0_d(lp, lf, H)
    # d 가 H 에 너무 근접하면 로그항 붕괴 방지
    eff_gap = max(H - d, 0.5)

    u_blend = wind_speed_10m * math.log(Z_BLEND / Z0_OPEN) / math.log(Z_REF / Z0_OPEN)
    u_H = u_blend * math.log(eff_gap / z0u) / math.log(max(Z_BLEND - d, eff_gap + 1.0) / z0u)
    u_H = max(u_H, 0.0)

    a = CIONCO_A0 + CIONCO_A1 * lf
    u_ped = u_H * math.exp(a * (Z_PED / H - 1.0))

    # 등가 지수 α: u_ped/u_10m = (1.5/10)^α  →  α = ln(ratio)/ln(0.15)
    ratio = max(u_ped / wind_speed_10m, 1e-4) if wind_speed_10m > 0 else 0.0
    alpha_equiv = math.log(ratio) / math.log(Z_PED / Z_REF) if ratio > 0 else float("nan")

    return RoughnessResult(
        pedestrian_wind_speed_ms=u_ped,
        z0_urban_m=z0u, disp_height_m=d,
        u_canopy_top_ms=u_H, canopy_atten_a=a,
        lambda_p=lp, lambda_f=lf, height_m=H,
        profile_exponent_equiv=alpha_equiv,
    )
