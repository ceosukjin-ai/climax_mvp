import SwiftUI

/// 몸씨 일본판 진입점 (2026-09-18).
/// 화면은 전부 웹(`WEB_URL`)이다. 네이티브는 위치·알림·오프라인 표시만 맡는다.
@main
struct MomssiJPApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var delegate

    var body: some Scene {
        WindowGroup {
            WebHostView()
                .ignoresSafeArea()          // 웹이 safe-area 를 직접 다룬다(viewport-fit=cover)
        }
    }
}

enum Config {
    /// 운영 주소. 개발 중 LAN 서버를 쓰려면 여기만 바꾼다.
    ///
    /// 앱은 **대시보드**(home.html)로 시작한다 — 설치한 사람에게는 지도보다
    /// "지금 나 위험한가"가 먼저다. 웹 링크(`/jp/`)는 지도 그대로 둔다:
    /// 링크를 받아 눌러보는 사람에게는 권한부터 묻는 첫 화면이 무겁다 (2026-09-18).
    static let webURL = URL(string: "https://api.climaxapp.kr/jp/home.html")!
    /// 앱에서 열렸음을 웹에 알린다 — 웹은 이 값으로 「홈 화면에 추가」 안내를 감춘다.
    static let appFlag = "?app=ios"
    /// 배경 알림이 서버에 직접 물을 때 쓴다(웹 화면 없이).
    static let apiBase = "https://api.climaxapp.kr"
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ app: UIApplication,
                     didFinishLaunchingWithOptions opts: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        PushBridge.shared.configure()
        // 걷는 동안 알림이 켜져 있으면 다시 건다 — 위치 이벤트로 앱이 깨어난 경우도 여기로 온다.
        LocationBridge.shared.resumeIfEnabled()
        return true
    }

    func application(_ app: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken token: Data) {
        PushBridge.shared.onToken(token)
    }

    func application(_ app: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        PushBridge.shared.onTokenFailed(error)
    }
}
