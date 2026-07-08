//
//  PVPTIClient.swift
//  ClimaX iOS — 백엔드 /api/v1/vpti/personalized 클라이언트.
//
import Foundation

enum PVPTIError: LocalizedError {
    case http(Int, String)
    case decoding(Error)

    var errorDescription: String? {
        switch self {
        case let .http(code, body): return "서버 오류(\(code)): \(body)"
        case let .decoding(err):    return "응답 해석 실패: \(err.localizedDescription)"
        }
    }
}

struct PVPTIClient {
    /// 백엔드 베이스 URL. 실기기 테스트 시 개발 PC의 LAN IP(https 권장) 로 교체.
    var baseURL: URL

    /// B2(권장): 좌표+생체신호만 보내면 서버가 scene/weather 자동 산출.
    func personalizedAuto(
        _ request: AutoPersonalizedVPTIRequest
    ) async throws -> PersonalizedVPTIResponse {
        try await post("api/v1/vpti/personalized/at", body: request)
    }

    /// B1(수동): scene/weather 를 클라이언트가 채워 보냄(테스트·비교용).
    func personalized(
        _ request: PersonalizedVPTIRequest
    ) async throws -> PersonalizedVPTIResponse {
        try await post("api/v1/vpti/personalized", body: request)
    }

    private func post<Body: Encodable>(
        _ path: String, body: Body
    ) async throws -> PersonalizedVPTIResponse {
        let url = baseURL.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        req.httpBody = try encoder.encode(body)

        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
        guard (200..<300).contains(code) else {
            throw PVPTIError.http(code, String(data: data, encoding: .utf8) ?? "")
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do { return try decoder.decode(PersonalizedVPTIResponse.self, from: data) }
        catch { throw PVPTIError.decoding(error) }
    }
}
