//
//  HealthKitManager.swift
//  ClimaX iOS — 애플워치(HealthKit) → PHI 입력 연동.
//
//  ios_handoff/HealthKitManager.swift 초안을 앱에 이식·정리한 버전(이 파일이 정본).
//  심박·활동에너지(kcal/min)·휴식심박을 읽어 BiometricsSample 로 만든다.
//
//  선행 조건
//    1) 유료 Apple Developer Program(HealthKit capability·실기기 필수).
//    2) Signing & Capabilities > + HealthKit.
//    3) Info.plist: NSHealthShareUsageDescription.
//    4) 실제 아이폰 + 페어링된 애플워치(시뮬레이터 X).
//
//  PHI 매핑:  hr ← heartRate,  activity ← activeEnergyBurned(kcal/min),
//            hr_rest ← restingHeartRate,  hr_max ← 없음(서버가 나이·성별식 콜드스타트).
//
import Foundation
import HealthKit

/// PHI 엔진으로 넘길 스냅샷. BiometricsDTO(hr, activity, hr_rest)와 매핑된다.
struct BiometricsSample {
    let hr: Double?            // 실시간 심박(bpm)
    let activity: Double?     // 활동강도 = active energy 소비율(kcal/min)
    let hrRest: Double?       // 휴식심박(bpm)
    let timestamp: Date

    var dto: BiometricsDTO {
        // hrMax 는 HealthKit 에 없음 → nil(서버 콜드스타트).
        BiometricsDTO(hr: hr, activity: activity, hrRest: hrRest, hrMax: nil)
    }
}

final class HealthKitManager {

    static let shared = HealthKitManager()
    private let store = HKHealthStore()

    /// 활동강도 A 계산 창(분). 이 구간 active energy 합 ÷ 분 = kcal/min.
    private let activityWindowMinutes: Double = 1.0

    private var hrType: HKQuantityType { HKQuantityType.quantityType(forIdentifier: .heartRate)! }
    private var energyType: HKQuantityType { HKQuantityType.quantityType(forIdentifier: .activeEnergyBurned)! }
    private var restingHRType: HKQuantityType { HKQuantityType.quantityType(forIdentifier: .restingHeartRate)! }
    private var bpmUnit: HKUnit { HKUnit.count().unitDivided(by: .minute()) }

    // MARK: - 1) 권한 요청

    func requestAuthorization() async throws {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw NSError(domain: "ClimaX.HealthKit", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "이 기기는 HealthKit 미지원"])
        }
        let readTypes: Set<HKObjectType> = [hrType, energyType, restingHRType]
        try await store.requestAuthorization(toShare: [], read: readTypes)
    }

    // MARK: - 2) 현재 스냅샷 (요청 시점 1회)

    /// 최신 심박 + 최근 창 활동에너지 소비율 + 최신 휴식심박 → BiometricsSample.
    func currentSample() async -> BiometricsSample {
        async let hr = latestQuantity(hrType, unit: bpmUnit)
        async let kcalPerMin = activeEnergyRate()
        async let hrRest = latestQuantity(restingHRType, unit: bpmUnit)
        return BiometricsSample(hr: await hr, activity: await kcalPerMin,
                                hrRest: await hrRest, timestamp: Date())
    }

    /// 지정 타입의 가장 최근 샘플 1개 값.
    private func latestQuantity(_ type: HKQuantityType, unit: HKUnit) async -> Double? {
        await withCheckedContinuation { cont in
            let sort = NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: false)
            let q = HKSampleQuery(sampleType: type, predicate: nil, limit: 1,
                                  sortDescriptors: [sort]) { _, samples, _ in
                let v = (samples?.first as? HKQuantitySample)?.quantity.doubleValue(for: unit)
                cont.resume(returning: v)
            }
            store.execute(q)
        }
    }

    /// 최근 activityWindowMinutes 분간 active energy 합 → kcal/min 소비율.
    private func activeEnergyRate() async -> Double? {
        await withCheckedContinuation { cont in
            let end = Date()
            let start = end.addingTimeInterval(-activityWindowMinutes * 60)
            let pred = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictEndDate)
            let q = HKStatisticsQuery(quantityType: energyType, quantitySamplePredicate: pred,
                                      options: .cumulativeSum) { _, stats, _ in
                guard let sum = stats?.sumQuantity() else { cont.resume(returning: nil); return }
                let kcal = sum.doubleValue(for: .kilocalorie())
                cont.resume(returning: kcal / self.activityWindowMinutes)
            }
            store.execute(q)
        }
    }
}
