//
//  ContentView.swift
//  ClimaX iOS — 최소 화면: 권한 요청 → 측정 → pVPTI 표시.
//
//  흐름: HealthKit 권한 → 애플워치 스냅샷(currentSample) + 로컬 프로필
//        → SampleScene(임시 장면) 합쳐 /vpti/personalized POST → pVPTI 표시.
//
import SwiftUI

@MainActor
final class PVPTIViewModel: ObservableObject {
    @Published var status: String = "건강 데이터 접근을 허용해 주세요."
    @Published var result: PersonalizedVPTIResponse?
    @Published var busy = false

    // 실기기 테스트 시 개발 PC의 LAN 주소로 교체(예: http://192.168.0.10:8000).
    private let client = PVPTIClient(baseURL: URL(string: "http://localhost:8000")!)

    // TODO: CoreLocation 으로 현재 위치 사용(지금은 데모 좌표 — 부산).
    private let location = LatLon(lat: 35.18901, lon: 129.10069)

    func authorize() async {
        do {
            try await HealthKitManager.shared.requestAuthorization()
            status = "권한 완료. '측정'을 눌러 pVPTI를 산출하세요."
        } catch {
            status = "권한 실패: \(error.localizedDescription)"
        }
    }

    func measure() async {
        busy = true
        defer { busy = false }
        status = "애플워치 데이터 읽는 중…"
        let sample = await HealthKitManager.shared.currentSample()

        // B2 자동 경로: 좌표+생체신호만 전송 → 서버가 Street View·기상 자동 산출.
        let request = AutoPersonalizedVPTIRequest(
            location: location,
            timestamp: nil,
            biometrics: sample.dto,
            profile: UserProfile.load().dto
        )
        do {
            status = "서버 계산 중…"
            result = try await client.personalizedAuto(request)
            status = "완료"
        } catch {
            status = error.localizedDescription
        }
    }
}

struct ContentView: View {
    @StateObject private var vm = PVPTIViewModel()

    var body: some View {
        VStack(spacing: 20) {
            Text("ClimaX pVPTI").font(.largeTitle).bold()
            Text(vm.status).font(.footnote).foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            if let r = vm.result {
                resultCard(r)
            }

            Spacer()

            Button("건강 데이터 허용") { Task { await vm.authorize() } }
                .buttonStyle(.bordered)
            Button(vm.busy ? "측정 중…" : "측정") { Task { await vm.measure() } }
                .buttonStyle(.borderedProminent)
                .disabled(vm.busy)
        }
        .padding()
    }

    @ViewBuilder
    private func resultCard(_ r: PersonalizedVPTIResponse) -> some View {
        VStack(spacing: 8) {
            Text("\(r.pvpti, specifier: "%.1f") °C").font(.system(size: 44, weight: .bold))
            Text("위험도: \(r.riskLevel)  ·  \(r.stressCategory)")
            HStack(spacing: 16) {
                stat("기준", r.baseVpti, "°C")
                stat("개인화Δ", r.deltaPersonalization, "°C")
                stat("심박부하", r.strainIndex, "")
            }.font(.caption)
            if let met = r.metabolicMet {
                Text("대사율 \(met, specifier: "%.2f") met  ·  hr_max \(r.hrMaxUsed ?? 0, specifier: "%.0f")")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding()
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 16))
    }

    private func stat(_ label: String, _ value: Double, _ unit: String) -> some View {
        VStack {
            Text(label).foregroundStyle(.secondary)
            Text("\(value, specifier: "%.2f")\(unit)")
        }
    }
}

#Preview {
    ContentView()
}
