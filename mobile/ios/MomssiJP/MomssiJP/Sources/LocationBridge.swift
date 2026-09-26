import CoreLocation
import UIKit
import UserNotifications

/// 위치 — 웹이 못 하는 첫 번째 것 (2026-09-18, 2026-09-26 배경 알림 추가).
///
/// 화면이 켜져 있을 때: `startUpdatingLocation` 으로 웹에 위치를 계속 넘긴다(웹이 30 m/10분 규칙으로 재계산).
/// 화면이 꺼져 있을 때: **significant-change**(약 500 m 이동)로만 깨어나 [`HeatAlert`] 가
/// 이 자리의 暑さ指数를 서버에 물어보고, 警戒 이상이면 알림을 띄운다.
///
/// ⚠️ 상시 GPS 추적은 쓰지 않는다. 배터리를 먹고 애플 심사(5.1.5)도 까다롭다.
final class LocationBridge: NSObject, CLLocationManagerDelegate {
    static let shared = LocationBridge()
    private let mgr = CLLocationManager()
    var onUpdate: (([String: Any]) -> Void)?
    private var backgroundOn = false

    override init() {
        super.init()
        mgr.delegate = self
        mgr.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        mgr.activityType = .fitness
        mgr.pausesLocationUpdatesAutomatically = true
    }

    func requestWhenInUse() {
        mgr.requestWhenInUseAuthorization()
        mgr.startUpdatingLocation()
    }

    /// 앱이 화면에서 내려가거나 웹이 추적을 끌 때 — 상시 GPS 를 켜 두지 않는다.
    func stopWhenInUse() {
        mgr.stopUpdatingLocation()
    }

    /// 걷는 동안 알림 켜기. 웹이 나이·질환·언어를 함께 넘긴다(기기 안에만 저장).
    func startSignificant(_ opts: [String: Any] = [:]) {
        HeatAlert.shared.saveSettings(opts, enabled: true)
        guard CLLocationManager.significantLocationChangeMonitoringAvailable() else { return }
        mgr.requestAlwaysAuthorization()
        mgr.allowsBackgroundLocationUpdates = true
        mgr.startMonitoringSignificantLocationChanges()
        backgroundOn = true
    }

    func stopSignificant() {
        HeatAlert.shared.saveSettings([:], enabled: false)
        mgr.stopMonitoringSignificantLocationChanges()
        mgr.allowsBackgroundLocationUpdates = false
        backgroundOn = false
    }

    /// 앱이 (위치 이벤트로) 다시 켜질 때 — iOS 는 모니터링을 이어 주지만 매니저는 새로 만들어야 한다.
    func resumeIfEnabled() {
        guard HeatAlert.shared.enabled,
              CLLocationManager.significantLocationChangeMonitoringAvailable() else { return }
        mgr.allowsBackgroundLocationUpdates = true
        mgr.startMonitoringSignificantLocationChanges()
        backgroundOn = true
    }

    func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        guard let l = locs.last, l.horizontalAccuracy > 0 else { return }
        // 화면이 꺼져 있으면 알림 판정. significant-change 값은 정확도가 수백 m 라 60 m 규칙을 적용하지 않는다.
        if UIApplication.shared.applicationState != .active {
            if HeatAlert.shared.enabled && l.horizontalAccuracy <= 1000 { HeatAlert.shared.check(l) }
            return
        }
        // 화면이 켜져 있을 때: 정확도가 나쁜 값은 버린다 — 웹 쪽 규칙(60 m)과 같게.
        guard l.horizontalAccuracy <= 60 else { return }
        onUpdate?(["fn": "location",
                   "lat": l.coordinate.latitude,
                   "lon": l.coordinate.longitude,
                   "acc": l.horizontalAccuracy,
                   "background": backgroundOn])
    }

    func locationManager(_ m: CLLocationManager, didFailWithError error: Error) {
        onUpdate?(["fn": "locationError", "message": error.localizedDescription])
    }

    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {
        // 3 = authorizedAlways, 4 = authorizedWhenInUse. 웹은 「항상」이 아니면 안내를 띄운다.
        onUpdate?(["fn": "locationAuth", "status": m.authorizationStatus.rawValue])
    }
}

/// 걷는 동안 더위 알림 (2026-09-26).
///
/// 서버 `/api/v1/jp/wbgt` 를 그대로 쓴다 — 가장 가까운 환경성 지점의 예측(전재)과 등급,
/// 발표 중인 熱中症警戒アラート. 등급은 나이·질환을 반영한다(화면과 같은 판정).
///
/// ⚠️ 기상업무법: 자체 예보는 알리지 않는다. 문구는 환경성 값의 전재 + 출처 표기.
/// 알림 규칙
///   · 警戒(warning) 이상일 때만.
///   · 등급이 올라가면 바로, 같은 등급이면 1시간에 한 번.
///   · 환경성 경보는 구역·날짜마다 한 번.
///   · 서버 조회는 3분에 한 번까지(깨어나는 이벤트가 몰릴 때 대비).
final class HeatAlert {
    static let shared = HeatAlert()
    private let d = UserDefaults.standard
    private let RANK = ["safe": 0, "caution": 1, "warning": 2, "severe": 3, "danger": 4]

    var enabled: Bool { d.bool(forKey: "ha.enabled") }

    func saveSettings(_ o: [String: Any], enabled on: Bool) {
        d.set(on, forKey: "ha.enabled")
        guard on else { return }
        if let a = o["age"] as? Int { d.set(a, forKey: "ha.age") } else { d.removeObject(forKey: "ha.age") }
        d.set((o["conditions"] as? [String]) ?? [], forKey: "ha.cond")
        d.set((o["lang"] as? String) ?? "ja", forKey: "ha.lang")
    }

    func check(_ loc: CLLocation) {
        let now = Date().timeIntervalSince1970
        guard now - d.double(forKey: "ha.lastQuery") > 180 else { return }
        d.set(now, forKey: "ha.lastQuery")

        var q = String(format: "lat=%.5f&lon=%.5f", loc.coordinate.latitude, loc.coordinate.longitude)
        if d.object(forKey: "ha.age") != nil { q += "&age=\(d.integer(forKey: "ha.age"))" }
        let cond = (d.array(forKey: "ha.cond") as? [String]) ?? []
        if !cond.isEmpty { q += "&conditions=" + cond.joined(separator: ",") }
        guard let url = URL(string: Config.apiBase + "/api/v1/jp/wbgt?" + q) else { return }

        // 깨어난 뒤 몇 초 안에 끝내야 한다 — 백그라운드 작업으로 감싼다.
        var task: UIBackgroundTaskIdentifier = .invalid
        task = UIApplication.shared.beginBackgroundTask { UIApplication.shared.endBackgroundTask(task) }
        var req = URLRequest(url: url); req.timeoutInterval = 15
        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            defer { UIApplication.shared.endBackgroundTask(task) }
            guard let self, let data,
                  let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            guard (j["ok"] as? Bool) == true else {
                // 일본 밖(부산 시험 등)에서는 공식값이 없다. DEBUG 빌드는 「깨어나서 서버까지 갔다」만 알린다.
                #if DEBUG
                self.notify(id: "test", title: "[テスト] 位置更新を受信",
                            body: String(format: "%.4f, %.4f · %@", loc.coordinate.latitude,
                                         loc.coordinate.longitude, (j["reason"] as? String) ?? "no data"))
                #endif
                return
            }
            self.decide(j)
        }.resume()
    }

    private func decide(_ j: [String: Any]) {
        let lang = d.string(forKey: "ha.lang") ?? "ja"
        let now = Date().timeIntervalSince1970
        let off = j["official"] as? [String: Any] ?? [:]
        let lv = j["level"] as? [String: Any] ?? [:]
        let code = lv["code"] as? String ?? "safe"
        let rank = RANK[code] ?? 0
        let wbgt = off["wbgt"] as? Double ?? 0
        let place = off["name"] as? String ?? ""
        let levelName = (lv[lang] as? String) ?? (lv["ja"] as? String) ?? ""
        let advice = ((lv["advice"] as? [String: Any])?[lang] as? String)
                  ?? ((lv["advice"] as? [String: Any])?["ja"] as? String) ?? ""

        // 1) 환경성 경보 — 구역·날짜마다 한 번
        if let a = j["alert"] as? [String: Any], let area = a["area"] as? String {
            let key = area + "|" + String((a["issued_at"] as? String ?? "").prefix(10))
            if d.string(forKey: "ha.alertKey") != key {
                d.set(key, forKey: "ha.alertKey")
                let special = (a["level"] as? String) == "special"
                notify(id: "alert",
                       title: L(lang, special ? "specialTitle" : "alertTitle") + "（\(area)）",
                       body: L(lang, "alertBody") + "\n" + L(lang, "src"))
            }
        }

        // 2) 등급 — 警戒 이상, 올라가면 바로 / 같으면 1시간에 한 번
        let lastRank = d.integer(forKey: "ha.lastRank")
        let lastAt = d.double(forKey: "ha.lastAt")
        let stale = now - lastAt > 3600
        // Xcode 에서 ▶ 로 설치한 빌드(DEBUG)는 「안전」에서도 알린다 — 9월 말엔 警戒가 드물어 시험이 안 된다.
        // 스토어 빌드(Release)는 警戒(2) 이상만.
        #if DEBUG
        let minRank = 0
        #else
        let minRank = 2
        #endif
        if rank >= minRank && (rank > lastRank || stale) {
            d.set(rank, forKey: "ha.lastRank"); d.set(now, forKey: "ha.lastAt")
            let title = String(format: L(lang, "levelTitle"), wbgt, levelName)
            notify(id: "level", title: title,
                   body: advice + "\n" + String(format: L(lang, "near"), place) + " · " + L(lang, "src"))
        } else if rank < minRank && stale {
            d.set(0, forKey: "ha.lastRank")      // 한 시간 넘게 괜찮았으면 다음 경계 때 다시 알린다
        }
    }

    private func notify(id: String, title: String, body: String) {
        let c = UNMutableNotificationContent()
        c.title = title; c.body = body; c.sound = .default
        c.threadIdentifier = "heat"
        let r = UNNotificationRequest(identifier: "heat.\(id).\(Int(Date().timeIntervalSince1970))",
                                      content: c, trigger: nil)
        UNUserNotificationCenter.current().add(r)
    }

    private func L(_ lang: String, _ k: String) -> String {
        let T: [String: [String: String]] = [
            "ja": ["levelTitle": "暑さ指数 %.1f・%@",
                   "near": "%@付近の予測", "src": "出典：環境省",
                   "alertTitle": "熱中症警戒アラート発表中", "specialTitle": "熱中症特別警戒アラート発表中",
                   "alertBody": "外出はなるべく控え、エアコンの効いた場所で過ごしましょう。"],
            "en": ["levelTitle": "Heat index %.1f · %@",
                   "near": "Forecast near %@", "src": "Source: MOE Japan",
                   "alertTitle": "Heat stroke alert issued", "specialTitle": "Special heat stroke alert issued",
                   "alertBody": "Avoid going out if you can and stay somewhere air-conditioned."],
            "ko": ["levelTitle": "더위지수 %.1f · %@",
                   "near": "%@ 부근 예측", "src": "출처: 일본 환경성",
                   "alertTitle": "열사병 경계경보 발표 중", "specialTitle": "열사병 특별경계경보 발표 중",
                   "alertBody": "되도록 외출을 삼가고 냉방이 되는 곳에 머무세요."]]
        return (T[lang] ?? T["ja"]!)[k] ?? (T["ja"]![k] ?? "")
    }
}
