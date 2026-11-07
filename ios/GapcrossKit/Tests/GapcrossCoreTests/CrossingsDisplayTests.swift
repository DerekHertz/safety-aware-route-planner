import Testing
@testable import GapcrossCore

@Suite struct CrossingsDisplayTests {
    @Test func markersMatchTheCrossingsAndCountsAreDerived() throws {
        let route = try Recorded.defaultRoute
        guard case let .assessed(markers, flagged, unknown) = CrossingsDisplay(route: route) else {
            Issue.record("expected markers"); return
        }
        // Counted here from the wire, independently of the display code.
        #expect(markers.map(\.key) == route.crossings.map(\.key))
        #expect(flagged == route.crossings.filter { $0.assessment.status == .flagged }.count)
        #expect(unknown == route.crossings.filter { $0.assessment.status == .unknown }.count)
        #expect((flagged, unknown) == (3, 1))
        #expect(markers.filter { $0.symbol == .flagged }.count == flagged)
    }

    @Test func rule2AFailedCheckShowsNoMarkersOrCounts() {
        #expect(CrossingsDisplay(route: lRoute(checked: false)) == .notChecked)
        let oneFailed = lRoute(crossings: [crossing("a", .flagged), crossing("b", .checkFailed)])
        #expect(CrossingsDisplay(route: oneFailed) == .notChecked)
        let fine = lRoute(crossings: [crossing("a", .flagged)])
        #expect(CrossingsDisplay(route: fine, outcome: .checkFailed) == .notChecked)
    }

    @Test func rule11ARerouteShowsNoMarkersOrClaimsEvenIfTheServerSentSome() {
        let rerouted = lRoute(label: .rerouted, crossings: [crossing("a", .flagged)], checked: true)
        #expect(CrossingsDisplay(route: rerouted) == .notAssessed)
    }

    @Test func noCrossingsOnACheckedRouteIsAnHonestZero() {
        #expect(CrossingsDisplay(route: lRoute()) == .assessed(markers: [], flagged: 0, unknown: 0))
    }
}
