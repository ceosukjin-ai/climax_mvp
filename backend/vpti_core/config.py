"""
vpti_core 전역 계수 설정 — 모든 수식 계수의 단일 출처(SSOT).

본 패키지는 세 건의 특허 명세서에 기재된 수식을 **원문 그대로** 구현하는
참조 구현이다. 따라서 계수는 두 부류로 엄격히 구분하여 관리한다.

  ✅ PATENT_CONFIRMED — 특허 명세서에 수치가 직접 명시된 값.
  ⚠️ UNCONFIRMED      — 특허가 수식의 "형태(form)"만 규정하고 수치는
                        제시하지 않은 값. 물리적 타당성에 근거한 가정값이며,
                        실증 데이터로 회귀 학습하여 대체되어야 한다.

각 계수에는 출처(특허/페이지/수학식 번호)를 주석으로 남긴다.
계수를 수정할 때는 반드시 해당 부류 표시도 함께 갱신할 것.

출처 문서
  - VSI  : P2026-0082-KR00 (다방향 시각 영상 기반 시각 환경 지수)
  - SMTI : APE-2026-0656   (외부 환경 변화를 반영한 표면 재질 기반 열 지수)
  - PWI  : 발명내용설명서_정숙진_260527
           (공공 기상 풍속의 보행자 공간 환경 기반 변환)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Season = Literal["summer", "winter", "transition"]


# =============================================================================
# VSI — 시각 환경 지수  (P2026-0082, 명세서 26쪽, 수학식 1)
# =============================================================================
@dataclass(frozen=True)
class VSIConfig:
    """VSI = w1·SVF + w2·(1−GVI) + w3·BVI."""

    # ✅ PATENT_CONFIRMED — 명세서 26쪽 "가중치(w1)는 0.5", "(w2)는 0.3",
    #    "(w3)는 0.2 로 설정될 수 있다." 제약: w1 ≥ w2 ≥ w3.
    w_svf: float = 0.5  # w1 : 하늘 가시율(SVF) 가중
    w_gvi: float = 0.3  # w2 : 식생 결핍도(1−GVI) 가중
    w_bvi: float = 0.2  # w3 : 건축 가시율(BVI) 가중

    # SVF 산출 방법 (특허 수치 아님 — 방법론 선택지):
    #   "zenith"    : 상향(천정) 영상 하늘비율 그대로 (특허 도 7 원문 구현).
    #                 단일 천정샷이라 저고도 차폐를 놓쳐 협곡에서 1.0으로 포화.
    #   "multiview" : 5뷰 상단을 입체각 가중 합산한 반구 SVF 근사 (기본).
    #                 천정 캡(상향뷰) + 하부 링(수평뷰 상단 절반)을 결합해 협곡
    #                 차폐를 반영 → 포화 완화. reconstruct_svf() 참조.
    svf_method: Literal["zenith", "multiview"] = "multiview"
    # 천정 캡 / 하부 링 경계 고도 [deg]. 상향뷰 fov=90·pitch=90, 수평뷰 fov=90·
    # pitch=0 이면 두 영역이 고도 45°에서 정확히 맞물린다 (Street View 기본값 기준).
    svf_horizon_split_deg: float = 45.0

    def __post_init__(self) -> None:
        # ✅ 특허 명시 제약: (w1 ≥ w2 ≥ w3)
        if not (self.w_svf >= self.w_gvi >= self.w_bvi):
            raise ValueError(
                "VSI 가중치는 w_svf ≥ w_gvi ≥ w_bvi 를 만족해야 함 "
                f"(특허 제약): ({self.w_svf}, {self.w_gvi}, {self.w_bvi})"
            )


# =============================================================================
# SMTI — 표면 재질 기반 열 지수  (APE-2026-0656, 수학식 1~6)
# =============================================================================
@dataclass(frozen=True)
class SMTIConfig:
    """f_i = α·A_i + β·S_i + γ·E_i,  SMTI = Σ P_i·f_i.

    A_i=(1−R_i)·I_i·(1−σ_i), S_i=C_i·(1−σ_i), E_i=ε_i·(1−σ_i).
    """

    # ⚠️ UNCONFIRMED — 특허(명세서 18~19쪽)는 흡수 가중치 α, 축열 가중치 β,
    #    방출 가중치 γ가 "계절별·시간대별로 조정 가능"하다고만 기재하고
    #    구체적 수치는 제시하지 않는다. 아래는 물리적 방향성에 근거한 가정값.
    #    - 여름 주간: 흡수(α) 지배적 / 야간: 방출(γ) 영향 ↑  (명세서 19쪽)
    #    - γ는 "음의 부호로 작용하여 체감 기후를 낮추는 방향" (명세서 18쪽) → 음수.
    absorption_weight: float = 1.0   # α : 흡수 가중치  (UNCONFIRMED)
    storage_weight: float = 1.0      # β : 축열 가중치  (UNCONFIRMED)
    emission_weight: float = -1.0    # γ : 방출 가중치, 음의 방향 (UNCONFIRMED)

    # ⚠️ UNCONFIRMED — 축열항 C_i(열용량)는 흡수/방출항(0~1)과 스케일이 크게
    #    다르므로(재질DB는 MJ/m³·K 단위), 동일 차원에서 결합하기 위한 정규화
    #    기준. 특허는 정규화 방법을 규정하지 않음. 물(4.18 MJ/m³·K)을 기준으로 사용.
    heat_capacity_reference: float = 4.18  # MJ/(m³·K)  (UNCONFIRMED)

    # 계절별 (α, β, γ) 오버라이드 — ⚠️ UNCONFIRMED (특허 "계절별 조정" 정성 기술만)
    seasonal_weights: dict[str, tuple[float, float, float]] = field(
        default_factory=lambda: {
            # 여름: 흡수 지배, 방출 페널티 큼 (열이 잘 안 빠짐)
            "summer": (1.0, 0.8, -1.2),
            # 겨울: 흡수 영향 작고 축열(보온)이 상대적으로 유리
            "winter": (0.6, 1.0, -0.8),
            "transition": (0.8, 0.9, -1.0),
        }
    )

    def weights_for(self, season: Season | None) -> tuple[float, float, float]:
        """계절별 (α, β, γ) 반환. season=None이면 기본값."""
        if season is None:
            return (
                self.absorption_weight,
                self.storage_weight,
                self.emission_weight,
            )
        return self.seasonal_weights.get(
            season,
            (self.absorption_weight, self.storage_weight, self.emission_weight),
        )


# =============================================================================
# PWI — 보행자 풍환경 지수  (발명내용설명서, 수학식 1~5)
# =============================================================================
@dataclass(frozen=True)
class PWIConfig:
    """u_p = PWI·u_ref,  PWI = (C_h·C_z·C_ch)^(1/3)·R_AI.

    부분 보정계수는 모두 지수함수 c = exp(β·(x − x_ref)) 형태(수학식 5)이므로
    항상 양수 → PWI > 0 → u_p ≥ 0 (물리적 일관성 보장).
    """

    # ----- 수학식 3 : von Mises 풍향 가중 -----
    # ⚠️ UNCONFIRMED — 집중도 파라미터 κ. 특허는 "κ ≥ 0 이고 값이 클수록
    #    풍향과 일치하는 view에 가중치가 집중된다"고만 기술. 수치 미제시.
    von_mises_kappa: float = 2.0  # κ  (UNCONFIRMED)

    # ----- 수학식 5 : 지수함수 부분 보정계수 c = exp(β·(x − x_ref)) -----
    # ⚠️ UNCONFIRMED — 민감도 계수 β_k (모두 양수) 및 기준값 x_ref.
    #    특허는 "β는 양의 민감도 계수, x_ref는 기준 값"이라고만 기술. 수치 미제시.
    #    부호(물리적 방향)는 특허 본문 설명에 따라 코드에서 결정:
    #      · SVF↑(하늘 개방) → 천정 개방으로 바람 유입↑ → C_z > 1  (+ 방향)
    #      · BVI↑(건물)      → 차폐로 바람↓            → C_h < 1  (− 방향)
    #      · GVI↑(수목)      → 차폐로 바람↓            → C_h < 1  (− 방향)
    beta_svf: float = 0.8   # 천정 개방 민감도   (UNCONFIRMED)
    beta_bvi: float = 1.0   # 건물 차폐 민감도   (UNCONFIRMED)
    beta_gvi: float = 0.5   # 수목 차폐 민감도   (UNCONFIRMED)
    beta_channel: float = 0.4  # channeling 민감도 (UNCONFIRMED)

    ref_svf: float = 0.4  # 기준 SVF  (UNCONFIRMED)
    ref_bvi: float = 0.3  # 기준 BVI  (UNCONFIRMED)
    ref_gvi: float = 0.2  # 기준 GVI  (UNCONFIRMED)

    # ----- 룰베이스 + AI 하이브리드 + Fallback (특허 '다'항) -----
    # ⚠️ UNCONFIRMED — AI 잔차 보정계수 R_AI의 신뢰도 임계치.
    #    특허: "신뢰도가 임계치 미만이거나 입력이 학습 분포 밖이면 R_AI=1".
    ai_confidence_threshold: float = 0.5  # (UNCONFIRMED)

    # 보정계수 안전 범위 — ⚠️ UNCONFIRMED. 지수함수 폭주 방지용 클램프.
    #    특허에 명시 없음. 물리적으로 비현실적인 증폭/감쇠를 막기 위한 가드.
    pwi_min: float = 0.05
    pwi_max: float = 3.0


# =============================================================================
# Solar — 일사 추정  (pvlib 청천일사 + KMA 운량 감쇠)
# =============================================================================
@dataclass(frozen=True)
class SolarConfig:
    """좌표·시각 → 태양위치·청천일사(pvlib) → KMA SKY 운량 감쇠.

    물리 모델은 모두 ✅ 표준(특허 외 영역이나 검증된 공개 모델 사용):
      · 태양위치/청천일사 : pvlib (NREL SPA + Ineichen-Perez clear-sky)
      · 구름 감쇠         : Kasten & Czeplak (1980)
                            G/G_clear = 1 − a·(N/8)^b,  a=0.75, b=3.4
      · 직달/산란 분리    : Erbs et al. (1982) diffuse-fraction 모델
    """

    # ✅ 표준 — pvlib 설정
    timezone: str = "Asia/Seoul"
    altitude_m: float = 10.0           # 해발고도 [m] (도시 보행면 근사)
    clearsky_model: str = "ineichen"   # pvlib Location.get_clearsky 모델

    # ✅ 표준 — Kasten & Czeplak (1980) 구름 감쇠 계수
    kc_a: float = 0.75
    kc_b: float = 3.4

    # KMA SKY 코드 → 전운량 비율(cloud fraction ∈ [0,1]).
    # KMA 정의: 1=맑음(전운량 0~5/10), 3=구름많음(6~8/10), 4=흐림(9~10/10).
    # 각 구간 중앙값을 대표 운량으로 사용 (10분위 기준 → /10).
    #   맑음 (0~5)/2 ≈ 2.5/10 = 0.25, 구름많음 (6~8)/2 ≈ 7/10 = 0.70,
    #   흐림 (9~10)/2 ≈ 9.5/10 = 0.95
    sky_cloud_fraction: dict[int, float] = field(
        default_factory=lambda: {1: 0.25, 3: 0.70, 4: 0.95}
    )
    default_cloud_fraction: float = 0.0  # SKY 미상 시 청천 가정


# =============================================================================
# MRT — 평균복사온도  (VDI 3787 Part 2 / Höppe 1992 / Thorsson 2007)
# =============================================================================
@dataclass(frozen=True)
class MRTConfig:
    """6방향 복사속 → Tmrt = (Sstr/(ε_p·σ))^0.25.

    인체·복사 상수는 ✅ 표준값(VDI 3787 Part 2, Fanger 1970):
      · a_k=0.7  : 인체 단파 흡수율
      · ε_p=0.97 : 인체 장파 방사율
      · 입체각 투영계수(서 있는 사람) 측면 0.22×4 + 상·하 0.06×2 = 1.0
      · 직달 투영면적계수 fp(β) = 0.308·cos(β(0.998−β²/50000)) (Fanger 1970)
      · 천공 방사율 ε_sky : Brunt(1932) 청천식 + Crawford&Duchon(1999) 운량보정

    ⚠️ UNCONFIRMED — 일사에 의한 지표면 승온(ΔTsurf) 단순화만 가정값.
    """

    # ✅ 표준 복사 상수
    a_k: float = 0.7        # 단파 흡수율
    eps_p: float = 0.97     # 인체 방사율
    f_side: float = 0.22    # 측면 4방향 각 투영계수
    f_up: float = 0.06      # 상향 투영계수
    f_down: float = 0.06    # 하향 투영계수

    # ✅ 표준 — Brunt 청천 천공방사율 계수: ε = c1 + c2·√e  (e: 수증기압 hPa)
    brunt_c1: float = 0.52
    brunt_c2: float = 0.065

    # 지표면 열물성 폴백 (재질에서 도출 못할 때) — ✅ 도시 평균 문헌값
    ground_albedo_default: float = 0.15
    ground_emissivity_default: float = 0.95

    # ⚠️ UNCONFIRMED — 일사에 의한 지표면 승온 단순화.
    #   ΔTsurf = surface_temp_rise_max · (1−albedo) · (GHI/ghi_reference) · (1−SVF보정)
    #   완전 에너지수지(대류·증발) 대신 일사 비례 1차 근사. 실증 데이터로 대체 대상.
    surface_temp_rise_max: float = 25.0   # 청천 정오 완전노출 불투수면 ΔT [°C]
    ghi_reference: float = 800.0          # ΔT 정규화 기준 일사 [W/m²]


# =============================================================================
# Comfort — 표준 체감지수  (UTCI Bröde 2012 / PET VDI 3787, pythermalcomfort)
# =============================================================================
@dataclass(frozen=True)
class ComfortConfig:
    """기온·습도·풍속·MRT → UTCI(우선) 또는 PET.

    체감지수 산출식 자체는 ✅ 검증 라이브러리(pythermalcomfort)에 위임하여
    임의 계수를 일절 쓰지 않는다. 아래는 라이브러리 호출 파라미터일 뿐이다.
    """

    index: Literal["utci", "pet"] = "utci"

    # UTCI 입력 유효범위 (Bröde 2012): 풍속 0.5~17 m/s. 범위 밖이면 클램프.
    utci_wind_min: float = 0.5
    utci_wind_max: float = 17.0

    # PET 기준 인체/활동 (pythermalcomfort.pet_steady 인자).
    # 표준 reference person 근사 — 활동 1.37 met(완보), 계절별 착의.
    pet_met: float = 1.37
    pet_clo_summer: float = 0.5
    pet_clo_winter: float = 1.0
    pet_clo_transition: float = 0.7
    pet_position: str = "standing"


# =============================================================================
# VPTI — 통합 체감 기후 지수  (세 특허 모두 "융합"만 기술, 결합 수식 없음)
# =============================================================================
@dataclass(frozen=True)
class VPTIConfig:
    """VPTI 통합 — 두 경로 지원, integration_mode 로 전환.

    · "mrt_utci" (기본) — 물리 기반: 일사 → MRT → UTCI/PET 표준 체감지수.
        VPTI = UTCI(또는 PET) [°C]. 결합이 표준 열생리 모델로 환원되어
        가정 계수가 (MRT의 ΔTsurf 외) 거의 없다.
    · "additive" (비교용) — 기존 가산형 체감기온 모델:
        VPTI = base_temp + Δ_VSI + Δ_SMTI + Δ_PWI
        ⚠️ UNCONFIRMED (전체) — 결합 수식이 특허 미규정이라 전부 가정값.
    """

    integration_mode: Literal["additive", "mrt_utci"] = "mrt_utci"

    # --- 가산형(additive) 전용 Δ 스케일 [°C] — ⚠️ UNCONFIRMED ---
    vsi_summer_delta: float = 5.0
    vsi_winter_delta: float = 2.0
    smti_summer_delta: float = 4.0
    smti_winter_delta: float = 1.5
    # 바람은 계절별 부호가 다름: 여름엔 냉각(쾌적), 겨울엔 체감 한파
    pwi_summer_delta: float = -3.0
    pwi_winter_delta: float = -8.0

    # 계절 판정 임계 기온 [°C]
    summer_temp_threshold: float = 23.0
    winter_temp_threshold: float = 10.0


# =============================================================================
# PHI — 생리 개인화  (애플워치 HealthKit → pVPTI, docs/PHI_HealthKit_통합계획.md)
# =============================================================================
@dataclass(frozen=True)
class PHIConfig:
    """생체신호 → 대사율/잔차 심박부하 개인화 계수.

    ① 대사율 환산은 ✅ 표준 단위·검증모델 입력, ② 잔차→위험 결합만 ⚠️ UNCONFIRMED.
    """

    # ✅ 표준 단위환산
    kcal_min_to_watt: float = 69.78         # 1 kcal/min = 69.78 W
    met_watt_per_m2: float = 58.15          # 1 met = 58.15 W/m² (ASHRAE 55)
    default_body_surface_area: float = 1.8  # DuBois 성인 근사 [m²]

    # met 유효범위 클램프 (pet_steady 수치 안정성)
    met_min: float = 0.8
    met_max: float = 6.0

    # ✅ 성별 hr_max 회귀식
    #   남성/기본 : Tanaka et al.(2001)  HRmax = 208 − 0.7×age
    #   여성       : Gulati et al.(2010)  HRmax = 206 − 0.88×age
    hr_max_tanaka_intercept: float = 208.0
    hr_max_tanaka_slope: float = 0.7
    hr_max_gulati_intercept: float = 206.0
    hr_max_gulati_slope: float = 0.88

    # ⚠️ UNCONFIRMED — 유산소 능력 기본값 [MET]. %HRR≈%VO₂R(ACSM/Swain 1997)에서
    #   "활동량으로 기대되는 심박"의 기준. 개인 체력 미상 시 성인 중간값. 교정 대상.
    vo2max_met: float = 10.0

    # ⚠️ UNCONFIRMED [VERIFY] — 잔차 심박부하=1(운동으로 설명 안 되는 최대 초과)일 때
    #   위험경계 앞당김 폭 [°C]. 환경 체감이 같아도 몸이 부담받으면 위험을 더 심각히
    #   분류. 실증(PHI 실증로깅)으로 교정 대상.
    strain_shift_max: float = 3.0


@dataclass(frozen=True)
class VPTICoreConfig:
    """모든 하위 설정을 묶는 최상위 설정 객체."""

    vsi: VSIConfig = field(default_factory=VSIConfig)
    smti: SMTIConfig = field(default_factory=SMTIConfig)
    pwi: PWIConfig = field(default_factory=PWIConfig)
    vpti: VPTIConfig = field(default_factory=VPTIConfig)
    solar: SolarConfig = field(default_factory=SolarConfig)
    mrt: MRTConfig = field(default_factory=MRTConfig)
    comfort: ComfortConfig = field(default_factory=ComfortConfig)
    phi: PHIConfig = field(default_factory=PHIConfig)


# 패키지 전역 기본 설정 (싱글톤처럼 import해서 사용)
DEFAULT_CONFIG = VPTICoreConfig()
