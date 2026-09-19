# 몸씨 일본판 — iOS 껍데기 (2026-09-18 착수)

웹 화면(`https://api.climaxapp.kr/jp/`)을 WKWebView 로 감싸고, **웹이 못 하는 것만** 네이티브로 넣는다.

## 왜 껍데기인가
일본 기능(지하가 경로·주변 장소·강아지 코스·WBGT)이 전부 웹에 있다. 한국 몸씨 iOS 는
한국 전용 기능(무더위쉼터·V월드·기상청)이 깊게 박혀 있어 떼어내는 쪽이 더 오래 걸린다.
화면을 그대로 쓰고, 웹의 한계만 메운다.

## 웹이 못 해서 네이티브로 넣는 것
| 기능 | 왜 |
|---|---|
| 백그라운드 위치 | 웹은 화면이 켜져 있을 때만 위치를 본다. 이동 중 자동 재측정이 불가 |
| 푸시 알림 | iOS 웹 푸시는 홈 화면 추가한 사람에게만. 위치 기반 경보 불가 |
| 오프라인 표시 | 지하철·지하가에서 통신이 끊긴다 |

**이 셋이 애플 4.2(Minimum Functionality) 통과 근거이기도 하다.**
"사파리로 보면 되는 걸 왜 앱으로 냈냐"에 대한 답이 이 셋이다.

## 구성
- `MomssiJPApp.swift` — 진입점
- `WebHost.swift` — WKWebView + JS 다리(`Native.*`)
- `LocationBridge.swift` — 위치 권한·백그라운드 갱신 → 웹으로 전달
- `PushBridge.swift` — 알림 권한·디바이스 토큰
- `Resources/Info.plist` — 권한 문구(일본어)

## 열기 (2026-09-19 — 프로젝트 파일 생성됨)
`MomssiJP.xcodeproj` 더블클릭 → 기기 선택 → ⌘R. 끝.
한국 몸씨 프로젝트(FileSystemSynchronized, objectVersion 77)를 본떠 만들었다:
번들 ID `kr.climaxapp.momssi.jp`, 개발 언어 ja, iOS 17.6+, iPhone 전용, HealthKit 없음, 아이콘은 몸씨 것.
`MomssiJP/` 폴더에 파일을 넣으면 자동으로 타깃에 들어간다(pbxproj 수정 불필요).

처음 열 때 한 번만: Signing & Capabilities → Team 선택 → **+ Capability → Background Modes** →
Location updates · Remote notifications 체크 (Info.plist 의 UIBackgroundModes 와 짝. 푸시는 Push Notifications capability 도 — 애플 개발자 콘솔 등록 뒤).

## 한계 (정직하게)
- 위치 백그라운드는 **significant-change** 만 쓴다. 상시 GPS 는 배터리를 먹고 애플 심사도 까다롭다.
  한국 몸씨가 상시 추적을 쓰지만 그건 보호 기능이 있어서다. 일본판 1차는 여기까지.
- 푸시는 토큰만 받아 서버에 넘긴다. 실제 발송은 서버 작업(미착수).
