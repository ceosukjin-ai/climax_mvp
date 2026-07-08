# ClimaX iOS 앱 골격 (Phase C)

애플워치(HealthKit) 생체신호 → 백엔드 `/api/v1/vpti/personalized` → **pVPTI** 표시.
네이티브 Swift(SwiftUI). `ios_handoff/HealthKitManager.swift` 초안을 이식·정리한 정본.

## 파일 구성
```
mobile/ios/ClimaX/
├─ ClimaXApp.swift               앱 진입점(@main)
├─ ContentView.swift             화면 + 뷰모델(권한→측정→pVPTI)
├─ HealthKit/HealthKitManager.swift   심박·활동·휴식심박 읽기(async)
├─ Networking/PVPTIClient.swift  POST /vpti/personalized
├─ Models/PVPTIModels.swift      요청·응답 DTO(snake_case 매핑)
├─ Models/UserProfile.swift      로컬 프로필(나이·성별·체격)
├─ Resources/Info.plist          NSHealthShareUsageDescription 등
└─ ClimaX.entitlements           HealthKit capability
```

## Xcode 프로젝트 만들기 (이 소스로)
> Windows에는 Xcode가 없으므로 **macOS에서** 진행. 이 폴더는 소스만 담고 있고
> `.xcodeproj`는 포함하지 않는다(손으로 만든 pbxproj는 깨지기 쉬워 제외).

1. Xcode > **New Project > iOS App**. Interface=**SwiftUI**, Language=**Swift**,
   Product Name=`ClimaX`.
2. 생성된 기본 `ContentView.swift`·`*App.swift`를 지우고, `mobile/ios/ClimaX/`의
   `.swift` 파일들을 타겟에 **Add Files**(Copy 안 함, 참조 권장).
3. **Signing & Capabilities > + Capability > HealthKit** 추가
   (→ `ClimaX.entitlements` 자동 생성. 이 저장소의 것과 동일하면 그대로 사용).
4. **Target > Info** 에 키 추가:
   - `NSHealthShareUsageDescription` = "체감기후(pVPTI) 개인화를 위해 심박·활동·휴식심박을 읽습니다."
   - (개발 중 http 접속 시) `App Transport Security Settings > Allow Local Networking = YES`.
5. **실기기 + 페어링된 애플워치**로 실행(시뮬레이터는 HealthKit 데이터 없음).
   유료 Apple Developer Program 필요.

## 백엔드 연결
- `PVPTIViewModel.client` 의 `baseURL` 을 개발 PC 주소로 교체.
  - 시뮬레이터: `http://localhost:8000`
  - 실기기: 같은 LAN의 PC IP, 예 `http://192.168.0.10:8000` (ATS 로컬 허용 필요).
- 백엔드 실행: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (backend/).

## 엔드포인트 (B2 자동 경로 사용 중)
앱은 **`POST /api/v1/vpti/personalized/at`** 를 쓴다 — 좌표+생체신호만 보내면 서버
orchestrator 가 Street View+SegFormer(공간)·KMA(기상)를 자동 산출해 pVPTI 를 낸다
(`PVPTIClient.personalizedAuto`). scene/weather 를 클라이언트가 채우던 임시 `SampleScene`
은 B2 에서 제거됨.

> 수동 엔드포인트 `POST /vpti/personalized`(B1)도 `PVPTIClient.personalized` 로 남겨둠
> (테스트·비교용).
>
> 서버 주의: 현재 `road_axis_deg=0.0` 가정값(OSM 도로축은 네트워크 호출이라 캐시 연동을
> 후속 과제로 남김), KMA 실황엔 SKY 없어 청천 가정.

## 다음 작업
- CoreLocation 으로 실제 현재 위치 사용(현재 `PVPTIViewModel.location` 데모 좌표 고정).
- 온보딩 프로필 입력 화면(`UserProfile` 저장).
- Phase D: 백그라운드 심박 수신(`HKObserverQuery` + `enableBackgroundDelivery`).
