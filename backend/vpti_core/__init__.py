"""
vpti_core — VSI·SMTI·PWI 특허 수식의 참조 구현 및 VPTI 통합 엔진.

세 건의 특허 명세서에 기재된 수식을 원문 그대로 구현한 패키지.
모든 계수는 config.py 한 곳에 모여 있으며, 특허에 수치가 명시된 값(✅)과
형태만 규정된 가정값(⚠️ UNCONFIRMED)을 주석으로 구분한다.

  · vsi  : 수학식 1            (P2026-0082)
  · smti : 수학식 1~6          (APE-2026-0656)
  · pwi  : 수학식 1~5          (발명내용설명서_정숙진_260527)
  · vpti : 세 지수 통합 엔진   (결합 수식은 특허 미규정 → 가산형 가정)
"""
from __future__ import annotations

from .comfort import (
    ComfortResult,
    compute_comfort,
    compute_pet,
    compute_utci,
)
from .config import (
    ComfortConfig,
    DEFAULT_CONFIG,
    MRTConfig,
    PHIConfig,
    PWIConfig,
    SMTIConfig,
    SolarConfig,
    VPTIConfig,
    VPTICoreConfig,
    VSIConfig,
)
from .materials import MATERIAL_DB, MaterialClass, ThermalProperties, get_properties
from .mrt import MRTResult, compute_mrt, ground_properties_from_materials
from .pwi import HorizontalView, PWIResult, build_horizontal_views, compute_pwi
from .smti import MaterialFraction, SMTIResult, compute_smti, shading_from_svf
from .solar import SolarResult, estimate_solar
from .vpti import (
    ThermalVPTIResult,
    VPTIResult,
    WeatherContext,
    compute_climate_index,
    compute_vpti,
    compute_vpti_thermal,
)
from .vsi import (
    VSIComponents,
    VSIResult,
    ViewSegmentation,
    compute_vsi,
    compute_vsi_from_components,
    extract_components,
    reconstruct_svf,
)
from .phi import (
    Biometrics,
    PersonalizedVPTIResult,
    PhysiologyProfile,
    body_surface_area,
    compute_pvpti,
    estimate_hr_max,
    evaluate_personalized,
    expected_hrr_from_met,
    heart_rate_reserve,
    metabolic_rate_from_activity,
    residual_strain,
)

__all__ = [
    # config
    "DEFAULT_CONFIG",
    "VPTICoreConfig",
    "VSIConfig",
    "SMTIConfig",
    "PWIConfig",
    "VPTIConfig",
    "SolarConfig",
    "MRTConfig",
    "ComfortConfig",
    "PHIConfig",
    # materials
    "MATERIAL_DB",
    "MaterialClass",
    "ThermalProperties",
    "get_properties",
    # vsi
    "ViewSegmentation",
    "VSIComponents",
    "VSIResult",
    "compute_vsi",
    "compute_vsi_from_components",
    "extract_components",
    "reconstruct_svf",
    # smti
    "MaterialFraction",
    "SMTIResult",
    "compute_smti",
    "shading_from_svf",
    # pwi
    "HorizontalView",
    "PWIResult",
    "compute_pwi",
    "build_horizontal_views",
    # solar (①)
    "SolarResult",
    "estimate_solar",
    # mrt (②)
    "MRTResult",
    "compute_mrt",
    "ground_properties_from_materials",
    # comfort (③)
    "ComfortResult",
    "compute_comfort",
    "compute_utci",
    "compute_pet",
    # vpti
    "WeatherContext",
    "VPTIResult",
    "compute_vpti",
    "ThermalVPTIResult",
    "compute_vpti_thermal",
    "compute_climate_index",
    # phi (생리 개인화 — pVPTI)
    "Biometrics",
    "PhysiologyProfile",
    "PersonalizedVPTIResult",
    "evaluate_personalized",
    "compute_pvpti",
    "body_surface_area",
    "metabolic_rate_from_activity",
    "estimate_hr_max",
    "heart_rate_reserve",
    "expected_hrr_from_met",
    "residual_strain",
]
