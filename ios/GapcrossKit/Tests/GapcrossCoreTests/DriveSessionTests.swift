import Foundation
import Testing
@testable import GapcrossCore

/// Records every call, so the rules are checked against what the app actually sent.
actor FakeService: GapcrossService {
    var reroutes: [LatLon] = []
    var reports: [DriveReport] = []
    let rerouted: Route

    init(rerouted: Route) { self.rerouted = rerouted }

    func trips() async throws -> [Trip] { [] }
    func defaultRoute(tripID: String) async throws -> Route { rerouted }
    func comparison(tripID: String) async throws -> Comparison { throw ServiceError.routeUnavailable }
    func reroute(tripID: String, from position: LatLon) async throws -> Route {
        reroutes.append(position)
        return rerouted
    }
    func send(_ report: DriveReport) async throws { reports.append(report) }
}

@MainActor @Suite struct DriveSessionTests {
    let flagged = lRoute(crossings: [crossing("a", .flagged), crossing("b", .unflagged, along: 800)])

    func session(_ feed: FeedKind, service: FakeService) -> DriveSession {
        DriveSession(tripID: "t", route: flagged, feed: feed, service: service,
                     departAt: Date(timeIntervalSince1970: 0))
    }

    @Test func rule13OffRouteOnlyShowsTheBannerUntilTheDriverTaps() async throws {
        let service = FakeService(rerouted: lRoute(label: .rerouted))
        let s = session(.simulated, service: service)
        for _ in 0..<120 { s.receive(.offRoute) }
        #expect(s.showsOffRouteBanner)
        #expect(await service.reroutes.isEmpty)
        #expect(s.route == flagged)

        try await s.recomputeTapped(at: point(east: 300, north: 60))
        #expect(await service.reroutes.count == 1)
        #expect(s.route.label == .rerouted)
        #expect(!s.showsOffRouteBanner)
    }

    @Test func rule11TheReroutedRouteShowsNoMarkers() async throws {
        let sneaky = lRoute(label: .rerouted, crossings: [crossing("a", .flagged)])
        let s = session(.real, service: FakeService(rerouted: sneaky))
        try await s.recomputeTapped(at: point(east: 300, north: 60))
        #expect(CrossingsDisplay(route: s.route) == .notAssessed)
        #expect(s.reportableCrossings.isEmpty)
    }

    @Test func rule12ASimulatedDriveNeverSendsADriveReport() async throws {
        let service = FakeService(rerouted: lRoute(label: .rerouted))
        let s = session(.simulated, service: service)
        s.receive(.arrived)
        #expect(s.phase == .done)
        try await s.submitReport(["a": .asDescribed])
        #expect(await service.reports.isEmpty)
    }

    @Test func aRealDriveAsksOncePerMarkedCrossingAndDropsUnanswered() async throws {
        let service = FakeService(rerouted: lRoute(label: .rerouted))
        let s = session(.real, service: service)
        s.receive(.arrived)
        #expect(s.phase == .askingForReport)
        #expect(s.reportableCrossings.map(\.key) == ["a", "b"])
        try await s.submitReport(["a": .notAsDescribed])
        let sent = await service.reports
        #expect(sent.count == 1)
        #expect(sent.first?.crossings.map(\.key) == ["a"])
        #expect(sent.first?.routeLabel == .example)
        #expect(s.phase == .done)
    }
}
