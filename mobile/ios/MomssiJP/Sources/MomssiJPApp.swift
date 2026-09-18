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
    static let webURL = URL(string: "https://api.climaxapp.kr/jp/")!
    /// 앱에서 열렸음을 웹에 알린다 — 웹은 이 값으로 「홈 화면에 추가」 안내를 감춘다.
    static let appFlag = "?app=ios"
}

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ app: UIApplication,
                     didFinishLaunchingWithOptions opts: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        PushBridge.shared.configure()
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
