//
//  UserProfile.swift
//  ClimaX iOS — 로컬 사용자 프로필(나이·성별·체격).
//
//  웹 프로필(web/profile.html, localStorage "climax_profile_v1")과 같은 성격.
//  개인화 계산에 필요한 최소 파생값만 서버로 보낸다(기저질환 등은 미전송).
//  실제 앱에선 온보딩 화면에서 입력받아 UserDefaults 에 저장. 골격은 기본값 제공.
//
import Foundation

struct UserProfile: Codable {
    var age: Int?
    var sex: String?          // "male" / "female"
    var heightCm: Double?
    var weightKg: Double?
    var observedHrMax: Double?

    /// 골격 기본 프로필 (온보딩 전 임시).
    static let placeholder = UserProfile(
        age: 40, sex: "male", heightCm: 175, weightKg: 72, observedHrMax: nil
    )

    private static let key = "climax_profile_v1"

    static func load() -> UserProfile {
        guard let data = UserDefaults.standard.data(forKey: key),
              let p = try? JSONDecoder().decode(UserProfile.self, from: data)
        else { return .placeholder }
        return p
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: UserProfile.key)
        }
    }

    var dto: ProfileDTO {
        ProfileDTO(age: age, sex: sex, heightCm: heightCm,
                   weightKg: weightKg, observedHrMax: observedHrMax)
    }
}
