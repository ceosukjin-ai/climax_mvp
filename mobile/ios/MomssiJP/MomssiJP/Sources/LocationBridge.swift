import CoreLocation

/// 위치 — 웹이 못 하는 첫 번째 것 (2026-09-18).
///
/// 웹의 `watchPosition` 은 화면이 켜져 있고 앱이 떠 있을 때만 돈다. 화면을 끄면 멈춘다.
/// 그래서 "걸어가는 동안 자동으로 다시 재기"가 웹으로는 불가능하다.
///
/// ⚠️ 상시 GPS 추적은 쓰지 않는다. 배터리를 먹고 애플 심사(5.1.5)도 까다롭다.
///    **significant-change**(약 500 m 이동 또는 5분)만 쓴다. 일본판 1차는 여기까지 —
///    한국 몸씨의 상시 추적은 보호자 연락이라는 분명한 사유가 있어서 허용된 것이다.
final class LocationBridge: NSObject, CLLocationManagerDelegate {
    static let shared = LocationBridge()
    private let mgr = CLLocationManager()
    var onUpdate: (([String: Any]) -> Void)?
    private var backgroundOn = false

    override init() {
        super.init()
        mgr.delegate = self
        mgr.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
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

    func startSignificant() {
        guard CLLocationManager.significantLocationChangeMonitoringAvailable() else { return }
        mgr.requestAlwaysAuthorization()
        mgr.allowsBackgroundLocationUpdates = true
        mgr.startMonitoringSignificantLocationChanges()
        backgroundOn = true
    }

    func stopSignificant() {
        mgr.stopMonitoringSignificantLocationChanges()
        mgr.allowsBackgroundLocationUpdates = false
        backgroundOn = false
    }

    func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
        guard let l = locs.last else { return }
        // 정확도가 나쁜 값은 버린다 — 웹 쪽 규칙(60 m)과 같게 맞춘다.
        guard l.horizontalAccuracy > 0, l.horizontalAccuracy <= 60 else { return }
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
        onUpdate?(["fn": "locationAuth", "status": m.authorizationStatus.rawValue])
    }
}
