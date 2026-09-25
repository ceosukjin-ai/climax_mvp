import SwiftUI
import WebKit

/// 웹 화면을 감싸는 그릇 (2026-09-18).
///
/// 웹과 네이티브의 다리는 **한 방향씩** 둔다:
///   · 웹 → 네이티브 : `window.webkit.messageHandlers.Native.postMessage({fn:"...", ...})`
///   · 네이티브 → 웹 : `window.Native.on(<JSON>)`  (웹이 이 함수를 정의해 두면 호출된다)
/// 웹이 그 함수를 정의하지 않아도 앱은 그냥 동작한다 — 웹을 먼저 고치지 않아도 되게 만든다.
struct WebHostView: UIViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        cfg.websiteDataStore = .default()           // 언어·설정을 기억하게 둔다
        cfg.userContentController.add(context.coordinator, name: "Native")

        let web = WKWebView(frame: .zero, configuration: cfg)
        web.navigationDelegate = context.coordinator
        web.allowsBackForwardNavigationGestures = true
        web.scrollView.bounces = false              // 지도 위에서 당겨지면 조작이 어긋난다
        // 시스템 글자 크기를 따라가지 않게 (한국 몸씨와 같은 방침 — 2026-09-17)
        web.configuration.preferences.setValue(false, forKey: "textInteractionEnabled")

        context.coordinator.web = web
        LocationBridge.shared.onUpdate = { [weak web] payload in
            guard let web else { return }
            Coordinator.send(web, payload)
        }
        PushBridge.shared.onEvent = { [weak web] payload in
            guard let web else { return }
            Coordinator.send(web, payload)
        }
        web.load(URLRequest(url: URL(string: Config.webURL.absoluteString + Config.appFlag)!))
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        weak var web: WKWebView?

        static func send(_ web: WKWebView, _ obj: [String: Any]) {
            guard let d = try? JSONSerialization.data(withJSONObject: obj),
                  let s = String(data: d, encoding: .utf8) else { return }
            // 웹이 Native.on 을 정의하지 않았으면 조용히 넘어간다.
            web.evaluateJavaScript("window.Native && window.Native.on && window.Native.on(\(s));")
        }

        func userContentController(_ c: WKUserContentController, didReceive msg: WKScriptMessage) {
            guard let body = msg.body as? [String: Any],
                  let fn = body["fn"] as? String else { return }
            switch fn {
            case "requestLocation":                 // 웹: 현재지 버튼
                LocationBridge.shared.requestWhenInUse()
            case "stopLocation":                    // 웹: 화면 숨김·추적 끄기
                LocationBridge.shared.stopWhenInUse()
            case "startBackground":                 // 웹: 보호/추적 켜기
                LocationBridge.shared.startSignificant()
            case "stopBackground":
                LocationBridge.shared.stopSignificant()
            case "requestPush":
                PushBridge.shared.requestAuthorization()
            case "haptic":
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
            default:
                break
            }
        }

        /// 통신이 끊겼을 때 — 지하철·지하가에서 흔하다. 빈 화면 대신 안내를 띄운다.
        func webView(_ web: WKWebView, didFail nav: WKNavigation!, withError error: Error) {
            showOffline(web)
        }
        func webView(_ web: WKWebView, didFailProvisionalNavigation nav: WKNavigation!, withError error: Error) {
            showOffline(web)
        }

        private func showOffline(_ web: WKWebView) {
            let html = """
            <html><head><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>body{margin:0;height:100%;display:flex;align-items:center;justify-content:center;
            background:#0f1720;color:#eaf1f7;font:16px -apple-system,"Hiragino Sans",sans-serif;
            text-align:center;padding:24px}button{margin-top:18px;padding:10px 18px;border-radius:10px;
            border:1px solid #2a3a49;background:#182430;color:#eaf1f7;font:inherit}</style></head>
            <body><div>接続できません<br><small style="color:#9fb3c4">
            地下や電波の弱い場所ではつながらないことがあります</small><br>
            <button onclick="location.href='\(Config.webURL.absoluteString)'">再読み込み</button>
            </div></body></html>
            """
            web.loadHTMLString(html, baseURL: nil)
        }
    }
}
