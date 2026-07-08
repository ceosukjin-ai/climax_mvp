# PHI 생리 개인화(애플워치 → pVPTI) 통합: 엔진 · API · iOS 골격

## 개요
애플워치 HealthKit(심박·활동에너지·휴식심박)을 `vpti_core`의 PET 경로에 반영해
**pVPTI(생리 개인화 체감기후 지수)**를 산출하는 전체 파이프라인을 추가합니다.
엔진 → 수동 API → 자동(GPS) API → iOS 앱 골격까지 배관이 이어집니다.

핵심 착안점: `activity(kcal/min) → 대사율 M`은 PET가 이미 필요로 하지만 상수(1.37 met)로
고정해둔 입력입니다. 즉 생체신호가 VPTI에 임의 계수를 덧붙이는 게 아니라, **검증모델(PET)
안의 가정 상수를 실측으로 교체**합니다.

> 브랜치: `feat/phi-healthkit-pvpti` → `main`
> 커밋: `541ec96` (24 files, +1950 / −5)

## 왜
- 무센서 체감기후(VPTI)를 **개인 생리 상태**로 개인화 → 같은 환경도 사람마다 다른 체감/위험도.
- 기존 연령·건강 개인화 계획(`docs/연령개인화_적용계획.md`)이 정한 "PET로 개인화" 원칙의
  실시간 축(생체신호)을 구현.

## 변경 사항

### 엔진 (`backend/vpti_core/`)
- **`phi.py` 신설**: `Biometrics`, `PhysiologyProfile`, `PersonalizedVPTIResult`,
  `evaluate_personalized`, `compute_pvpti`.
- **대사율 개인화**: `activity → met`로 PET 재계산(met 효과 순수 분리).
- **잔차 심박부하**: `관측 HRR − 활동량 기대 HRR`(%HRR≈%VO₂R, ACSM/Swain 1997)로 운동/더위를
  분리 → 선선한 날 빠른 걸음의 허위경보 제거. `activity` 없으면 `strain=0`(환경 PET만).
- **성별 hr_max 콜드스타트**: 여성 Gulati(206−0.88·age) / 남성·기본 Tanaka(208−0.7·age).
- `config.PHIConfig`: ✅ 표준 단위환산·회귀식 vs ⚠️ UNCONFIRMED(VO₂max·strain 결합) 구분.
- `demo.py`에 pVPTI 시나리오 출력 추가.

### API (`backend/app/`)
- **`POST /api/v1/vpti/personalized`** (B1, 수동 입력) — `vpti_core`를 서빙 계층에 처음 연결.
- **`POST /api/v1/vpti/personalized/at`** (B2, 자동) — `orchestrator.compute_personalized`가
  좌표+생체신호만으로 Street View·기상 자동 조회 후 pVPTI 산출.
- 스키마: `BiometricsIn`, `ProfileDerivedIn`, `PersonalizedVPTI(Request|Response)`,
  `AutoPersonalizedVPTIRequest`.

### 도로축 캐시 연동 (`orchestrator.py`, `cache.py`)
- `PanoAnalysisCache`에 `road_axis_deg`/`road_axis_source` 추가(기본값 → 하위호환).
- `_analyze_views`가 miss 시 OSM 도로축을 SegFormer와 **동시** 계산해 panoId 영구캐시에 저장.
- PWI Δθ(수학식 2)에 실측 도로축 반영. `road_axis_deg=0.0` 가정 제거.

### iOS 골격 (`mobile/ios/`, 네이티브 Swift/SwiftUI)
- `HealthKitManager`(async), `PVPTIClient`, DTO, `UserProfile`, SwiftUI 화면, `Info.plist`,
  `entitlements`, README. 자동 엔드포인트(`personalizedAuto`) 사용.
- `.gitignore`의 RN 잔재 `mobile/ios/` 무시 규칙 해제(네이티브 앱 소스 위치).

### 문서
- `docs/PHI_HealthKit_통합계획.md`(전체 계획), `ios_handoff/`(iOS 핸드오프 초안).

## 캐시 불변성 (CLAUDE.md 핵심 설계 준수)
- 자동 pVPTI는 `compute()`와 **같은 분리 캐시**(공간=panoId 영구 / 기상=10분)를 재사용하고
  **새 캐시 키를 추가하지 않습니다**.
- 도로축(네트워크 호출)은 **miss 때 1회만** 계산해 공간 캐시에 저장 → 재방문(hit)은
  Overpass를 타지 않아 **"재방문 <100ms"** 유지.

## 프라이버시
- biometrics는 요청 계산에만 사용하고 **저장·로깅하지 않습니다(계산 후 폐기)**.
- 프로필은 최소 파생값(나이·성별·체격)만 전송.

## 테스트
- `pytest tests/test_phi.py` — **20 passed**.
- `test_services.py`(orchestrator 자동경로·도로축 캐시 하위호환), `test_api.py`(엔드포인트) 추가.
- 전체 영향 스위트 green (기존 stale `test_root` 1건 제외 — 정적 마운트 관련, 본 PR과 무관).
- iOS는 Windows/무-Xcode 환경이라 컴파일 미검증(스키마 대조로 정합성 확인). 실기기+애플워치
  +유료 개발자계정에서 빌드 필요.

## 알려진 한계 / 후속
- `sky_code=None`(청천 가정) — KMA 실황엔 SKY 없음. 초단기예보 SKY 연계는 후속.
- iOS CoreLocation 미구현(데모 좌표 고정), 온보딩 프로필 화면 미구현.
- HRR→위험경계 결합계수·VO₂max 기본값은 잠정([VERIFY]) — 실증 로깅으로 교정 필요.
- Phase D(백그라운드 심박 수신) 미착수.
- 기존 미사용 import 정리는 별도 커밋 권장(본 PR 범위 밖).

## ⚠️ 배포 주의
자동 배포가 `main` 기준이면 이 브랜치 병합 전까지 라이브 영향 없음.
병합 시 새 엔드포인트가 `vpti_core` PET 경로를 서빙 계층에 처음 노출하므로,
스테이징에서 `/vpti/personalized/at` 동작 확인 후 병합 권장.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
