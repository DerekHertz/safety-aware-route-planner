import Foundation
import Testing
@testable import GapcrossCore

@Suite struct SimulatedDriveTests {
    @Test(arguments: ["default", "alternative"])
    func theSimulatedDriveReachesTheDestinationWithTurnsInOrder(_ which: String) throws {
        let comparison = try Recorded.comparison
        let route = which == "default" ? comparison._default : try #require(comparison.alternative)
        var follower = Follower(route: route)
        var announced: [Step] = []  // two roundabout steps share their text, so compare whole steps
        var last: GuidanceState?
        for f in SimulatedFeed.track(route: route, every: 1, from: Date(timeIntervalSince1970: 0)) {
            let state = follower.update(f)
            if case let .following(next, _) = state, announced.last != next {
                announced.append(next)
            }
            #expect(state != .offRoute)
            last = state
        }
        #expect(last == .arrived)
        #expect(announced == Array(route.steps.dropFirst()))  // every step after the departure, in order
    }

    @Test func playbackSpeedNeverChangesTheEta() throws {
        let route = try Recorded.defaultRoute
        let start = Date(timeIntervalSince1970: 0)
        let track = SimulatedFeed.track(route: route, every: 1, from: start)
        #expect(abs(track.last!.time.timeIntervalSince(start) - route.etaS) <= 1)
        let fast = SimulatedFeed(route: route, speedMultiplier: 8)
        #expect(fast.route.etaS == route.etaS)
        #expect(fast.wallInterval(forRouteSeconds: 8) == 1)
    }

    @Test(.timeLimit(.minutes(1))) func theFeedStartsPausesAndRestarts() async throws {
        let route = lRoute()
        let feed = SimulatedFeed(route: route, speedMultiplier: 1000, every: 10)
        var it = feed.fixes.makeAsyncIterator()
        feed.start()
        let first = await it.next()
        #expect(first?.coordinate == route.geometry.first)
        feed.pause()
        #expect(feed.isPaused)
        feed.restart()
        #expect(!feed.isPaused)
        var backAtTheStart = false
        while let f = await it.next() {
            if f.coordinate == route.geometry.first { backAtTheStart = true; break }
        }
        #expect(backAtTheStart)
    }
}
