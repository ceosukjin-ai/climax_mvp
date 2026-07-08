# iOS HealthKit 연동 핸드오프 (애플워치 → PHI)

애플워치 데이터를 ClimaX PHI 엔진에 넣기 위한 iOS 측 코드 초안.
**배포 본체(`D:\ClimaX_MVP`)로 옮겨 Xcode에서 빌드**한다. 여기(vpti_core)선 실행 불가.

## 파일
- `HealthKitManager.swift` — 권한 요청 → 심박·활동에너지·휴식심박 읽기 → `BiometricsSample`.

## 앱에 넣기 전 체크리스트
1. **유료 Apple Developer Program**($99/년) — HealthKit capability·실기기 필수.
2. Xcode > Target > **Signing & Capabilities > + HealthKit**.
3. `Info.plist` → `NSHealthShareUsageDescription`
   = "체감기후(PHI) 개인화를 위해 심박·활동 데이터를 읽습니다."
4. **실제 아이폰 + 페어링된 애플워치**에서 테스트(시뮬레이터 X).
5. 앱 첫 실행 시 `requestAuthorization` → 사용자가 심박·활동에너지·휴식심박 "허용".

## 데이터 흐름
```
애플워치 ──자동──▶ 아이폰 건강앱 ──HealthKitManager──▶ BiometricsSample(JSON)
   ──▶ 백엔드 ──▶ Biometrics(hr, activity, hr_rest)
   ──▶ VPTIEngine.evaluate(scene, weather, bio=bio, profile=profile) ──▶ pVPTI
```

## PHI 매핑
| BiometricsSample | HealthKit | PHI(Biometrics) |
|---|---|---|
| `hr` | `heartRate` (bpm) | `hr` |
| `activity` | `activeEnergyBurned` (kcal/min) | `activity` |
| `hrRest` | `restingHeartRate` (bpm) | `hr_rest` |
| — | (없음) | `hr_max` → 관측 최대/측정값·성별식 콜드스타트 |

## 알아둘 제약
- 일반 착용 중 심박은 **몇 분 간격 배치**(실시간 스트림 아님). 로깅·교정엔 충분.
- 초 단위 라이브가 필요하면 워치 워크아웃 세션(별도 워치 앱) — 실시간 경보용, 후순위.

관련: `../vpti_core/PHI_실증로깅_교정계획_HealthKit.md`(로깅 스키마·교정),
`../vpti_core/PHI_심박개인화_설계.md`(엔진 설계), `../vpti_core/phi.py`(구현).
