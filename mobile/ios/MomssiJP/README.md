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

## 대표님이 하실 것 (제가 못 하는 것)
1. Xcode → File → New → Project → **App**, Interface **SwiftUI**, 이름 `MomssiJP`
2. 번들 ID: `kr.climaxapp.momssi.jp` (한국 앱 `com.sukjin.ClimaX` 와 **달라야 한다**. 같으면 덮어쓴다)
3. 이 폴더의 `Sources/*.swift` 를 프로젝트에 끌어다 넣기
4. Info.plist 에 이 폴더 `Resources/Info.plist` 의 키들을 병합
5. Signing & Capabilities → **Background Modes** → Location updates, Remote notifications 체크
6. ⌘R

## 한계 (정직하게)
- 위치 백그라운드는 **significant-change** 만 쓴다. 상시 GPS 는 배터리를 먹고 애플 심사도 까다롭다.
  한국 몸씨가 상시 추적을 쓰지만 그건 보호 기능이 있어서다. 일본판 1차는 여기까지.
- 푸시는 토큰만 받아 서버에 넘긴다. 실제 발송은 서버 작업(미착수).
