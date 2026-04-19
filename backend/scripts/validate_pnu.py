"""
PNU 실측 데이터로 VSI-MRT 회귀 R² 재현.

실행:
    cd backend
    PYTHONPATH=. python scripts/validate_pnu.py

논문 보고 값:
    R² = 0.222, p = 0.011 (VSI vs MRT)
    MAE ≈ 2.0°C (VSI-based PET estimation)

이 스크립트는 `data/pnu/measurements.csv`를 읽어 동일한 회귀를 수행하고
MVP 엔진이 논문 결과를 재현하는지 확인합니다. MVP 심사·실증 단계에서
"이 엔진은 논문 수치를 재현합니다"의 직접 증거가 됩니다.

주의: 제공된 CSV는 논문 3.2절의 분포를 기반으로 재구성한 합성
데이터입니다. 실제 측정 CSV를 확보하면 이 파일을 덮어쓰면 됩니다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from app.core.vsi import compute_vsi_from_components

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "pnu" / "measurements.csv"


def main() -> int:
    if not DATA_PATH.exists():
        print(f"[ERROR] Data file not found: {DATA_PATH}")
        return 1

    df = pd.read_csv(DATA_PATH)
    print(f"Loaded {len(df)} points from {DATA_PATH.name}")
    print(f"Urban type distribution:\n{df['urban_type'].value_counts()}\n")

    # VSI 계산 (엔진 사용)
    vsi_values = []
    for _, row in df.iterrows():
        result = compute_vsi_from_components(
            svf=row["svf"], gvi=row["gvi"], bvi=row["bvi"]
        )
        vsi_values.append(result.vsi)

    df["vsi_computed"] = vsi_values

    # 회귀
    slope, intercept, r_value, p_value, std_err = stats.linregress(
        df["vsi_computed"], df["mrt_c"]
    )
    r_squared = r_value**2

    print("=" * 60)
    print("VSI → MRT 회귀 결과")
    print("=" * 60)
    print(f"  R²     : {r_squared:.4f}  (논문 보고값: 0.222)")
    print(f"  p-value: {p_value:.4f}  (논문 보고값: 0.011)")
    print(f"  slope  : {slope:.3f}")
    print(f"  std err: {std_err:.3f}")

    # PET 회귀 (사용 가능하면)
    if "pet_c" in df.columns:
        pet_slope, pet_int, pet_r, pet_p, _ = stats.linregress(
            df["vsi_computed"], df["pet_c"]
        )
        predicted_pet = pet_slope * df["vsi_computed"] + pet_int
        mae = np.mean(np.abs(df["pet_c"] - predicted_pet))
        rmse = np.sqrt(np.mean((df["pet_c"] - predicted_pet) ** 2))

        print()
        print("VSI → PET 회귀 결과")
        print("=" * 60)
        print(f"  R²  : {pet_r**2:.4f}  (논문 보고값: 0.105)")
        print(f"  MAE : {mae:.2f}°C  (논문 보고값: ≈2.0°C)")
        print(f"  RMSE: {rmse:.2f}°C  (논문 보고값: ≈2.6°C)")

    # 유형별 VSI 분포
    print()
    print("유형별 VSI 분포 (median, IQR)")
    print("=" * 60)
    for utype in ["Building Canyon", "Building Road", "Green"]:
        subset = df[df["urban_type"] == utype]["vsi_computed"]
        if len(subset) == 0:
            continue
        q25, q50, q75 = np.percentile(subset, [25, 50, 75])
        print(f"  {utype:20s} median={q50:.3f}, IQR=[{q25:.3f}, {q75:.3f}]")

    # 임계값 기반 분류 검증
    print()
    print("VSI 임계값 분류 (논문 Table 3)")
    print("=" * 60)
    for _, row in df.iterrows():
        r = compute_vsi_from_components(row["svf"], row["gvi"], row["bvi"])
        print(
            f"  {row['point_id']:8s} {row['urban_type']:16s} "
            f"VSI={r.vsi:.3f} → {r.category:8s} MRT={row['mrt_c']:.1f}°C"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
