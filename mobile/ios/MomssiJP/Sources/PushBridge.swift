import UIKit
import UserNotifications

/// 알림 — 웹이 못 하는 두 번째 것 (2026-09-18).
///
/// iOS 웹 푸시는 홈 화면에 추가한 사람에게만 간다. 대부분은 추가하지 않는다.
/// 그리고 서버가 사용자 위치를 모르므로 "지금 당신이 선 자리가 위험하다"를 못 보낸다.
///
/// 지금은 **토큰만 받아 서버에 넘긴다.** 발송은 서버 작업(미착수).
/// ⚠️ 일본은 기상업무법상 자체 예보 공표에 허가가 필요하다. 알림 문구는
///    "지금 暑さ指数 33" 같은 **실황**이거나 환경성 경보 **전재**여야 한다.
///    "내일 위험합니다" 같은 자체 예보는 허가 전까지 보내지 않는다.
final class PushBridge: NSObject, UNUserNotificationCenterDelegate {
    static let shared = PushBridge()
    var onEvent: (([String: Any]) -> Void)?

    func configure() {
        UNUserNotificationCenter.current().delegate = self
    }

    func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { ok, _ in
            DispatchQueue.main.async {
                self.onEvent?(["fn": "pushAuth", "granted": ok])
                if ok { UIApplication.shared.registerForRemoteNotifications() }
            }
        }
    }

    func onToken(_ token: Data) {
        let hex = token.map { String(format: "%02x", $0) }.joined()
        onEvent?(["fn": "pushToken", "token": hex])
        // TODO: 서버 등록 엔드포인트가 생기면 여기서 POST.
    }

    func onTokenFailed(_ error: Error) {
        onEvent?(["fn": "pushToken", "error": error.localizedDescription])
    }

    /// 앱이 떠 있을 때도 알림을 보이게 한다 — 안 그러면 조용히 삼켜진다.
    func userNotificationCenter(_ c: UNUserNotificationCenter,
                                willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound])
    }
}
