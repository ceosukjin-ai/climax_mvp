# 애플워치 HealthKit → pVPTI 통합 계획

> 상태: **Phase A 착수**. 모바일 스택·데이터 경로·PHI 위치·hr_max·프라이버시 결정 완료.
> 관련: `ios_handoff/HealthKitManager.swift`(iOS 초안), `docs/연령개인화_적용계획.md`(PET 개인화 원칙),
> `backend/vpti_core/`(특허 충실 엔진).

---

## 0. 목표

애플워치가 아이폰 건강앱에 쌓는 **심박·활동에너지·휴식심박**을 읽어, `vpti_core`의 새 **PHI 엔진**에
`Biometrics`로 넣고 **pVPTI(생리 개인화 체감기후 지수)**를 산출한다.

핵심 착안점: `activity(kcal/min) → 대사율 M`은 **PET가 이미 필요로 하지만 현재 상수(1.37 met)로
고정해둔 입력**이다. 즉 biometrics는 VPTI에 임의 계수를 덧붙이는 게 아니라, 검증모델(PET) 안의
가정 상수를 실측으로 교체한다. 이는 `연령개인화_적용계획.md`의 "PET로 개인화" 원칙과 일치한다.

---

## 1. 확정된 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 모바일 스택 | **네이티브 Swift (iOS 전용)** | `mobile/`는 빈 폴더(그린필드). 새 Xcode 앱이 `HealthKitManager.swift`를 직접 호스팅, 브리지 불필요 |
| 데이터 경로 | **요청 시 스냅샷 우선** (`fetchCurrentSample`) | 백그라운드 배치 수신(Phase D)은 후순위 |
| PHI 위치 | **`vpti_core`에 신설 + 새 REST 엔드포인트** | 특허 충실 패키지에 엔진을 두고, 서빙 계층(`app.core`)에 처음으로 `vpti_core` PET 경로를 연결 |
| `hr_max` | **연령식 콜드스타트** `hr_max ≈ 208 − 0.7×age` (Tanaka 2001) + 관측 최댓값 보정 | HealthKit에 hr_max 없음 |
| 프라이버시 | **계산 후 폐기** (서버 미저장) | 프로필 문서의 "민감정보 최소화" 원칙 |

---

## 2. 데이터 흐름

```
Apple Watch → iPhone 건강앱 → [HealthKitManager] → BiometricsSample(JSON)
  → POST /api/v1/vpti/personalized  (biometrics + 최소 프로필파생값)
  → 매핑 → vpti_core.Biometrics(hr, activity, hr_rest, hr_max)
  → PHI 엔진:
       ① activity(kcal/min) → 대사율 M(W/m²) → PET met 입력   ← 물리적 정공법
       ② hr, hr_rest, hr_max → HRR(심박여유율) → 열스트레스 수정자
  → pVPTI(°C) + 생리적 위험도  →  biometrics 폐기(미저장)
```

## 3. PHI 데이터 매핑 (Swift ↔ Python)

| BiometricsSample (Swift) | HealthKit | vpti_core.Biometrics |
|---|---|---|
| `hr` | `heartRate` (bpm) | `hr` |
| `activity` | `activeEnergyBurned` (kcal/min) | `activity` |
| `hrRest` | `restingHeartRate` (bpm) | `hr_rest` |
| — (없음) | — | `hr_max` ← `208 − 0.7×age` / 관측최댓값 |

---

## 4. PHI 산식 (초안 — 결합계수는 전부 ⚠️ UNCONFIRMED / [VERIFY])

```
① 대사율:  M[W/m²] = activity[kcal/min] × 69.78 / A_body
             A_body = DuBois(키,몸무게) ≈ 1.8 m² (프로필 없으면 기본)
             met    = M / 58.15           (1 met ≈ 58.15 W/m²)
             안정시 활동 ≈ 1 met ≈ 58 W/m²
② HRR:     HRR = clamp((hr − hr_rest) / (hr_max − hr_rest), 0, 1)
             hr_max = max(208 − 0.7×age,  관측최댓값)   (Tanaka 2001)
③ pVPTI:   PET_personalized = compute_pet(Ta, Tmrt, u_p, RH, met=met)
             + HRR 기반 위험경계 앞당김 (생리적 스트레스 반영, 잠정계수)
```

- ①의 `69.78`(kcal/min→W), `58.15`(met 정의)는 ✅ 표준 단위환산.
- ②의 `208 − 0.7×age`는 ✅ Tanaka et al.(2001) 공개 회귀식.
- ③의 HRR→위험경계 결합계수는 ⚠️ UNCONFIRMED — `PHI_실증로깅` 계획의 실측으로 교정.

---

## 5. 구현 단계

### Phase A — PHI 엔진 (`backend/vpti_core/`)  ← 현재
- `phi.py` 신설: `Biometrics` dataclass + `metabolic_rate_from_activity()` +
  `estimate_hr_max()` + `heart_rate_reserve()` + `evaluate_personalized()` →
  `PersonalizedVPTIResult(pvpti, strain_index, ...)`.
- `config.py`: `PHIConfig` 추가(확정값/가정값 주석 구분), `VPTICoreConfig`에 연결.
- `__init__.py`: 신규 심볼 export.
- `tests/test_phi.py`: 콜드스타트·HRR 경계·M 변환·PET 개인화 단위 테스트.

### Phase B — 백엔드 API
- `app/schemas/`: `BiometricsIn`, `ProfileDerivedIn`(age·성별·취약플래그), `PersonalizedVPTIResponse`.
- `app/api/routes.py`: `POST /api/v1/vpti/personalized` — `vpti_core` PET+PHI 경로 호출.
  biometrics는 응답 생성 후 폐기, 로그에 원시 HR 미기록.

### Phase C — iOS 네이티브 앱 골격
- 새 Xcode 프로젝트 + `HealthKitManager.swift` 이식.
- `Info.plist` `NSHealthShareUsageDescription`, HealthKit capability.
- `fetchCurrentSample` → `/vpti/personalized` POST → pVPTI 표시.

### Phase D — 백그라운드 배치 수신 (후순위)
- `startBackgroundHeartRateDelivery` → 주기적 POST(로깅·교정용).

---

## 6. 미해결 / 주의

- HRR→위험경계 결합계수는 잠정값 → 실측(`PHI_실증로깅` 계획)으로 교정 필요.
- `activity` 기반 met는 최근 창(1분) 소비율이라 순간 활동에 민감 → 스무딩 검토.
- iOS는 유료 개발자계정·실기기 필수(시뮬레이터 불가), 스냅샷 심박은 몇 분 간격 배치.
- pVPTI는 건강 안내로 쓸 경우 배포 전 의료·보건 전문가 검토 필요.
