import Foundation

/// One position from a feed. The follower cannot tell a simulated feed from a real one.
public struct Fix: Equatable, Sendable {
    public let coordinate: LatLon
    public let course: Double?
    public let speed: Double?
    public let time: Date

    public init(coordinate: LatLon, course: Double?, speed: Double?, time: Date) {
        self.coordinate = coordinate
        self.course = course
        self.speed = speed
        self.time = time
    }
}

public protocol PositionFeed: Sendable {
    var fixes: AsyncStream<Fix> { get }
}

public enum GuidanceState: Equatable, Sendable {
    case following(next: Step, distanceToItM: Double)
    case offRoute
    case arrived
}

/// Follows one route from a stream of fixes. It never changes the route: off route, it only says so.
public struct Follower: Sendable {
    public let route: Route
    /// Off route means farther than this from the route... (starting guess, tuned on the first drive)
    public let offRouteDistanceM: Double
    /// ...for longer than this.
    public let offRouteSeconds: TimeInterval
    static let arrivalM = 25.0
    static let lookBehindM = 50.0
    static let lookAheadM = 400.0

    let line: Polyline
    let stepAlong: [Double]  // each step's position on this line
    var along = 0.0
    var awaySince: Date?
    var hasArrived = false

    public init(route: Route, offRouteDistanceM: Double = 40, offRouteSeconds: TimeInterval = 5) {
        self.route = route
        self.offRouteDistanceM = offRouteDistanceM
        self.offRouteSeconds = offRouteSeconds
        let line = Polyline(route.geometry)
        self.line = line
        stepAlong = route.steps.map { step in
            let near = step.alongM
            return line.project(step.location, within: (near - 100)...(near + 100)).along
        }
    }

    public mutating func update(_ fix: Fix) -> GuidanceState {
        if hasArrived { return .arrived }
        var hit = line.project(fix.coordinate, within: (along - Self.lookBehindM)...(along + Self.lookAheadM))
        if hit.offset > offRouteDistanceM {
            let anywhere = line.project(fix.coordinate)
            if anywhere.offset <= offRouteDistanceM { hit = anywhere }
        }
        if hit.offset > offRouteDistanceM {
            let since = awaySince ?? fix.time
            awaySince = since
            if fix.time.timeIntervalSince(since) > offRouteSeconds { return .offRoute }
            return following
        }
        awaySince = nil
        along = hit.along
        if along >= line.length - Self.arrivalM {
            hasArrived = true
            return .arrived
        }
        return following
    }

    private var following: GuidanceState {
        guard let k = stepAlong.indices.first(where: { stepAlong[$0] > along }) else { return .arrived }
        return .following(next: route.steps[k], distanceToItM: stepAlong[k] - along)
    }

    /// Guidance for every fix the feed delivers.
    public static func states(route: Route, feed: some PositionFeed) -> AsyncStream<GuidanceState> {
        let fixes = feed.fixes
        return AsyncStream { continuation in
            let task = Task {
                var follower = Follower(route: route)
                for await fix in fixes { continuation.yield(follower.update(fix)) }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
