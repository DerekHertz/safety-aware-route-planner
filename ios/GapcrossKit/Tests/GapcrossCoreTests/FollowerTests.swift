import Testing
@testable import GapcrossCore

@Suite struct FollowerTests {
    @Test func followingGivesTheNextStepAndTheDistanceToIt() {
        var follower = Follower(route: lRoute())
        let state = follower.update(fix(point(east: 400, north: 3), at: 0))
        guard case let .following(next, distance) = state else { Issue.record("\(state)"); return }
        #expect(next.instruction == "Turn left onto North Street")
        #expect(abs(distance - 600) < 1)
    }

    @Test func offRouteNeedsMoreThan40MetersForMoreThan5Seconds() {
        var follower = Follower(route: lRoute())
        _ = follower.update(fix(point(east: 300, north: 0), at: 0))
        let away = point(east: 300, north: 60)
        #expect(follower.update(fix(away, at: 1)) != .offRoute)
        #expect(follower.update(fix(away, at: 6)) != .offRoute)     // exactly 5 s: not yet
        #expect(follower.update(fix(away, at: 6.5)) == .offRoute)
        #expect(follower.update(fix(point(east: 300, north: 39), at: 7)) != .offRoute)  // back within 40 m
    }

    @Test func arrivesAtTheEndOfTheRoute() {
        var follower = Follower(route: lRoute())
        _ = follower.update(fix(point(east: 1000, north: 200), at: 0))
        #expect(follower.update(fix(point(east: 1000, north: 495), at: 1)) == .arrived)
    }
}
