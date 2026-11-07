import Foundation
import GapcrossAPI

public enum ServiceError: Error, Equatable {
    /// HTTP 503: the routing source failed; the app offers retry.
    case routeUnavailable
    case notFound
    case unexpected(statusCode: Int)
}

/// Everything the app asks of the service, through the client generated from api/openapi.json.
public protocol GapcrossService: Sendable {
    func trips() async throws -> [Trip]
    func defaultRoute(tripID: String) async throws -> Route
    /// With avoidance on. With it off the app calls only `defaultRoute`.
    func comparison(tripID: String) async throws -> Comparison
    func reroute(tripID: String, from position: LatLon) async throws -> Route
    func send(_ report: DriveReport) async throws
}

public struct LiveService: GapcrossService {
    let client: Client

    public init(baseURL: URL = URL(string: "http://localhost:8000")!) {
        client = .service(at: baseURL)
    }

    public func trips() async throws -> [Trip] {
        switch try await client.listTripsTripsGet() {
        case let .ok(ok): return try ok.body.json
        case let .undocumented(code, _): throw ServiceError.unexpected(statusCode: code)
        }
    }

    public func defaultRoute(tripID: String) async throws -> Route {
        switch try await client.defaultRouteTripsTripIdDefaultPost(path: .init(tripId: tripID)) {
        case let .ok(ok): return try ok.body.json
        case .serviceUnavailable: throw ServiceError.routeUnavailable
        case .notFound: throw ServiceError.notFound
        case .unprocessableContent: throw ServiceError.unexpected(statusCode: 422)
        case let .undocumented(code, _): throw ServiceError.unexpected(statusCode: code)
        }
    }

    public func comparison(tripID: String) async throws -> Comparison {
        switch try await client.comparisonTripsTripIdComparisonPost(path: .init(tripId: tripID),
                                                                     body: .json(.init(avoid: true))) {
        case let .ok(ok): return try ok.body.json
        case .serviceUnavailable: throw ServiceError.routeUnavailable
        case .notFound: throw ServiceError.notFound
        case .unprocessableContent: throw ServiceError.unexpected(statusCode: 422)
        case let .undocumented(code, _): throw ServiceError.unexpected(statusCode: code)
        }
    }

    public func reroute(tripID: String, from position: LatLon) async throws -> Route {
        switch try await client.rerouteRouteTripsTripIdReroutePost(path: .init(tripId: tripID),
                                                                   body: .json(.init(position: position))) {
        case let .ok(ok): return try ok.body.json
        case .serviceUnavailable: throw ServiceError.routeUnavailable
        case .notFound: throw ServiceError.notFound
        case .unprocessableContent: throw ServiceError.unexpected(statusCode: 422)
        case let .undocumented(code, _): throw ServiceError.unexpected(statusCode: code)
        }
    }

    public func send(_ report: DriveReport) async throws {
        switch try await client.driveReportDriveReportsPost(body: .json(report)) {
        case .noContent: return
        case .notFound: throw ServiceError.notFound
        case .unprocessableContent: throw ServiceError.unexpected(statusCode: 422)
        case let .undocumented(code, _): throw ServiceError.unexpected(statusCode: code)
        }
    }
}
