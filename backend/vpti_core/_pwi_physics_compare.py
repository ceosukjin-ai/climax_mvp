"""PWI 물리(거칠기-밀도) 비교 — 지금 '앱 실서비스' 방식(app.core.pwi) vs 물리 방식."""
import math
from app.core.pwi import compute_pwi, WindCondition

KAPPA=0.40; A_MAC=4.43; CD=1.2; BETA=1.0
Z0_OPEN=0.03; Z_REF=10.0; Z_BLEND=100.0; Z_PED=1.5

def macdonald_z0_d(lp, lf, H):
    d_over_H = min(max(1.0 + A_MAC**(-lp)*(lp-1.0), 0.0), 0.95)
    inner = 0.5*BETA*CD/KAPPA**2*(1.0-d_over_H)*lf
    z0_over_H = (1.0-d_over_H)*math.exp(-(inner)**-0.5) if inner>0 else 0.0
    return z0_over_H*H, d_over_H*H

def physics_ped_wind(u10, lp, lf, H):
    z0u, d = macdonald_z0_d(lp, lf, H)
    u_blend = u10*math.log(Z_BLEND/Z0_OPEN)/math.log(Z_REF/Z0_OPEN)
    u_H = u_blend*math.log((H-d)/z0u)/math.log((Z_BLEND-d)/z0u)
    a = 1.0 + 3.0*lf
    return u_H*math.exp(a*(Z_PED/H-1.0)), z0u, d

CASES=[
    ("개방 대로", 0.80,0.10,0.05, 0.15,0.08,12.0),
    ("일반 가로", 0.60,0.30,0.10, 0.35,0.30,18.0),
    ("건물 협곡", 0.40,0.55,0.05, 0.55,0.55,40.0),
]
U=3.0
print(f"기준: AWS 10m {U} m/s\n")
print(f"{'가로유형':<8} {'SVF':>4} {'BVI':>4} | {'지금 앱 u_p':>10} {'(기준%)':>7} | {'물리 u_p':>8} {'(기준%)':>7} | {'물리 z0':>7}")
print("-"*80)
for name,svf,bvi,gvi,lp,lf,H in CASES:
    r = compute_pwi(WindCondition(U,270.0,30.0), svf, bvi)
    up_now = r.pedestrian_wind_speed_ms
    up_phy, z0u, d = physics_ped_wind(U, lp, lf, H)
    print(f"{name:<8} {svf:>4.2f} {bvi:>4.2f} | {up_now:>8.2f}m/s {up_now/U*100:>5.0f}% | "
          f"{up_phy:>6.2f}m/s {up_phy/U*100:>5.0f}% | {z0u:>6.2f}m")
