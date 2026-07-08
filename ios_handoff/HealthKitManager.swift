//
//  HealthKitManager.swift
//  ClimaX — 애플워치(HealthKit) → PHI 입력 연동 핸드오프 초안
//
//  목적: 아이폰 건강앱에 쌓인 애플워치 데이터(심박·활동에너지·휴식심박)를 읽어
//        vpti_core PHI 엔진의 Biometrics(hr, activity, hr_rest)로 넘긴다.
//
//  ⚠️ 핸드오프 초안 — 실제 앱(D:\ClimaX_MVP)에 이식 후 Xcode에서 빌드/테스트.
//     여기(vpti_core)에선 실행 불가(Xcode 없음). Claude Code로 옮겨 사용.
//
//  선행 조건
//    1) 유료 Apple Developer Program 멤버십(HealthKit capability·실기기 필수).
//    2) Xcode > Signing & Capabilities > + HealthKit 추가.
//    3) Info.plist 에 사용 사유 문구:
//         NSHealthShareUsageDescription = "체감기후(PHI) 개인화를 위해 심박·활동 데이터를 읽습니다."
//    4) 실제 아이폰 + 페어링된 애플워치에서만 동작(시뮬레이터 X).
//
//  PHI 매핑
//    hr        ← heartRate            (bpm)
//    activity  ← activeEnergyBurned   (kcal/min, 최근 창의 소비율)
//    hr_rest   ← restingHeartRate     (bpm, 최신 일일값)
//    hr_max    ← HealthKit에 없음 → 관측 최대치/측정값 또는 성별식 콜드스타트(엔진 기본)
//

import Foundation
import HealthKit

/// PHI 엔진 Biometrics 로 넘길 스냅샷. Python Biometrics(hr, activity, hr_rest) 와 1:1.
struct BiometricsSample: Codable {
    let hr: Double?            // 실시간 심박(bpm)
    let activity: Double?     // 활동강도 A = active energy 소비율(kcal/min)
    let hrRest: Double?       // 휴식심박(bpm)
    let timestamp: Date

    enum CodingKeys: String, CodingKey {
        case hr, activity
        case hrRest = "hr_rest"
        case timestamp
    }
}

final class HealthKitManager {

    static let shared = HealthKitManager()
    private let store = HKHealthStore()

    // 활동강도 A 를 계산할 창(분). 이 구간 active energy 합 ÷ 분 = kcal/min.
    private let activityWindowMinutes: Double = 1.0

    private var hrType: HKQuantityType { HKQuantityType.quantityType(forIdentifier: .heartRate)! }
    private var energyType: HKQuantityType { HKQuantityType.quantityType(forIdentifier: .activeEnergyBurned)! }
    private var restingHRType: HKQuantityType { HKQuantityType.quantityType(forIdentifier: .restingHeartRate)! }

    private var bpmUnit: HKUnit { HKUnit.count().unitDivided(by: .minute()) }

    // MARK: - 1) 권한 요청

    func requestAuthorization(_ completion: @escaping (Bool, Error?) -> Void) {
        guard HKHealthStore.isHealthDataAvailable() else {
            completion(false, NSError(domain: "ClimaX.HealthKit", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "이 기기는 HealthKit 미지원"]))
            return
        }
        let readTypes: Set<HKObjectType> = [hrType, energyType, restingHRType]
        store.requestAuthorization(toShare: nil, read: readTypes) { ok, err in
            DispatchQueue.main.async { completion(ok, err) }
        }
    }

    // MARK: - 2) 현재 스냅샷 읽기 (요청 시점 1회)

    /// 최신 심박 + 최근 창 활동에너지 소비율 + 최신 휴식심박을 모아 BiometricsSample 생성.
    func fetchCurrentSample(_ completion: @escaping (BiometricsSample) -> Void) {
        let group = DispatchGroup()
        var hr: Double?
        var kcalPerMin: Double?
        var hrRest: Double?

        group.enter()
        latestQuantity(hrType, unit: bpmUnit) { v in hr = v; group.leave() }

        group.enter()
        activeEnergyRate { v in kcalPerMin = v; group.leave() }

        group.enter()
        latestQuantity(restingHRType, unit: bpmUnit) { v in hrRest = v; group.leave() }

        group.notify(queue: .main) {
            completion(BiometricsSample(hr: hr, activity: kcalPerMin,
                                        hrRest: hrRest, timestamp: Date()))
        }
    }

    /// 지정 타입의 가장 최근 샘플 1개 값.
    private func latestQuantity(_ type: HKQuantityType, unit: HKUnit,
                                _ completion: @escaping (Double?) -> Void) {
        let sort = NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: false)
        let q = HKSampleQuery(sampleType: type, predicate: nil, limit: 1,
                              sortDescriptors: [sort]) { _, samples, _ in
            let v = (samples?.first as? HKQuantitySample)?.quantity.doubleValue(for: unit)
            completion(v)
        }
        store.execute(q)
    }

    /// 최근 activityWindowMinutes 분간 active energy 합 → kcal/min 소비율.
    private func activeEnergyRate(_ completion: @escaping (Double?) -> Void) {
        let end = Date()
        let start = end.addingTimeInterval(-activityWindowMinutes * 60)
        let pred = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictEndDate)
        let q = HKStatisticsQuery(quantityType: energyType, quantitySamplePredicate: pred,
                                  options: .cumulativeSum) { _, stats, _ in
            guard let sum = stats?.sumQuantity() else { completion(nil); return }
            let kcal = sum.doubleValue(for: .kilocalorie())
            completion(kcal / self.activityWindowMinutes)   // kcal/min
        }
        store.execute(q)
    }

    // MARK: - 3) 백그라운드 증분 수신 (배치 샘플 → 신규만)
    //
    //  일반 착용 중 애플워치 심박은 실시간 스트림이 아니라 몇 분 간격 배치로 들어온다.
    //  로깅/교정엔 이걸로 충분. (초 단위 라이브는 워치 워크아웃 세션이 필요 — 후순위.)

    func startBackgroundHeartRateDelivery(onNewSample: @escaping (BiometricsSample) -> Void) {
        let observer = HKObserverQuery(sampleType: hrType, predicate: nil) { [weak self] _, done, _ in
            self?.fetchCurrentSample { sample in onNewSample(sample) }
            done()   // 반드시 호출(백그라운드 재호출 유지)
        }
        store.execute(observer)
        store.enableBackgroundDelivery(for: hrType, frequency: .immediate) { _, _ in }
    }
}

//
//  다음 단계(엔진 연결)
//  ---------------------------------------------------------------------------
//  ClimaX 엔진은 Python(vpti_core)이므로, 이 스냅샷을 백엔드로 POST 하거나
//  로컬 로깅 스키마(§4, PHI_실증로깅_교정계획_HealthKit.md)로 저장한다.
//
//    let json = try JSONEncoder().encode(sample)   // { "hr":.., "activity":.., "hr_rest":.. }
//    // → POST /biometrics  또는  경로별 VPTI 요청 바디에 첨부
//
//  백엔드에서 Biometrics(hr=..., activity=..., hr_rest=...) 로 만들어
//  VPTIEngine.evaluate(scene, weather, bio=bio, profile=profile) 호출 → pVPTI.
//
