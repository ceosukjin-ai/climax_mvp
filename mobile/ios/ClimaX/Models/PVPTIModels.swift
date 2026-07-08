//
//  PVPTIModels.swift
//  ClimaX iOS — 백엔드 /api/v1/vpti/personalized 요청·응답 DTO.
//
//  Swift 프로퍼티는 camelCase 로 두고, 인코더/디코더의 snake_case 변환 전략으로
//  서버 스키마(app/schemas/vpti.py 의 PersonalizedVPTI*)와 매핑한다.
//  (temperatureC ↔ temperature_c, hrRest ↔ hr_rest, baseVpti ↔ base_vpti …)
//
import Foundation

struct LatLon: Codable {
    let lat: Double
    let lon: Double
}

struct ViewSegmentationDTO: Codable {
    let direction: String          // up / front / back / left / right
    let skyRatio: Double
    let vegetationRatio: Double
    let buildingRatio: Double
}

struct MaterialFractionDTO: Codable {
    let material: String           // asphalt / concrete / vegetation / …
    let fraction: Double
}

struct WeatherDTO: Codable {
    let temperatureC: Double
    let humidityPct: Double
    let windSpeedMs: Double
    let windDirectionDeg: Double
}

/// 애플워치 스냅샷. 전부 Optional(부분 결측 허용). hrMax 는 HealthKit 에 없어 nil →
/// 서버가 나이·성별식으로 콜드스타트.
struct BiometricsDTO: Codable {
    let hr: Double?
    let activity: Double?          // kcal/min
    let hrRest: Double?
    let hrMax: Double?
}

/// 개인화 파생값 — 민감정보 최소화(나이·성별·체격만).
struct ProfileDTO: Codable {
    let age: Int?
    let sex: String?               // "male" / "female"
    let heightCm: Double?
    let weightKg: Double?
    let observedHrMax: Double?
}

struct PersonalizedVPTIRequest: Codable {
    let location: LatLon
    let views: [ViewSegmentationDTO]
    let materials: [MaterialFractionDTO]
    let weather: WeatherDTO
    let roadAxisDeg: Double
    let timestamp: String?         // ISO8601 (nil 이면 서버가 현재시각)
    let skyCode: Int?              // KMA SKY 1/3/4
    let biometrics: BiometricsDTO
    let profile: ProfileDTO?
}

/// 자동 pVPTI 요청(B2) — 좌표+생체신호만. 서버가 scene/weather 자동 산출.
struct AutoPersonalizedVPTIRequest: Codable {
    let location: LatLon
    let timestamp: String?
    let biometrics: BiometricsDTO
    let profile: ProfileDTO?
}

struct PersonalizedVPTIResponse: Codable {
    let pvpti: Double
    let baseVpti: Double
    let deltaPersonalization: Double
    let riskLevel: String
    let baseRiskLevel: String
    let strainIndex: Double
    let observedHrr: Double?
    let expectedHrr: Double?
    let metabolicMet: Double?
    let hrMaxUsed: Double?
    let season: String
    let stressCategory: String
    // comfort(dict)는 상세 진단용이라 골격에선 디코딩 생략(추가 키는 무시됨).
}
