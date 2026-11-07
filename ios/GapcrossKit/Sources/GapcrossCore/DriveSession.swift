import Foundation
import Observation

public enum FeedKind: Sendable { case simulated, real }

public enum DrivePhase: Equatable, Sendable { case driving, askingForReport, done }

/// One drive of one route. The route changes only when the driver taps recompute (rule 13), and a
/// drive report is sent only after a real drive (rule 12).
@MainActor @Observable
public final class DriveSession {
    public private(set) var route: Route
    public private(set) var guidance: GuidanceState?
    public private(set) var showsOffRouteBanner = false
    public private(set) var phase = DrivePhase.driving
    public private(set) var isRecomputing = false

    public let tripID: String
    public let feed: FeedKind
    let service: any GapcrossService
    let departAt: Date

    public init(tripID: String, route: Route, feed: FeedKind, service: any GapcrossService, departAt: Date) {
        self.tripID = tripID
        self.route = route
        self.feed = feed
        self.service = service
        self.departAt = departAt
    }

    /// The crossings a real drive asks about: one tap each. None on a rerouted route (rule 11).
    public var reportableCrossings: [Marker] { CrossingsDisplay(route: route).markers }

    public func receive(_ state: GuidanceState) {
        guard phase == .driving else { return }
        guidance = state
        switch state {
        case .offRoute:
            showsOffRouteBanner = true
        case .following:
            showsOffRouteBanner = false
        case .arrived:
            showsOffRouteBanner = false
            phase = feed == .real && !reportableCrossings.isEmpty ? .askingForReport : .done
        }
    }

    public func recomputeTapped(at position: LatLon) async throws {
        isRecomputing = true
        defer { isRecomputing = false }
        route = try await service.reroute(tripID: tripID, from: position)
        showsOffRouteBanner = false
        guidance = nil
    }

    /// Unanswered crossings are dropped. Does nothing after a simulated drive.
    public func submitReport(_ answers: [String: CrossingReport.AnswerPayload]) async throws {
        guard feed == .real, phase == .askingForReport else { return }
        let crossings = reportableCrossings.compactMap { marker in
            answers[marker.key].map { CrossingReport(answer: $0, key: marker.key) }
        }
        let report = DriveReport(crossings: crossings, departAt: departAt,
                                 routeLabel: .init(rawValue: route.label.rawValue)!, tripId: tripID)
        try await service.send(report)
        phase = .done
    }

    public func skipReport() {
        if phase == .askingForReport { phase = .done }
    }
}
