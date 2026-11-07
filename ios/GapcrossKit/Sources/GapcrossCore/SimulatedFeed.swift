import Foundation

/// Drives along a route at its own average speed, in route time. `speedMultiplier` only paces the
/// playback against the wall clock: the fixes, and so the ETA, are the same at any speed.
public final class SimulatedFeed: PositionFeed, @unchecked Sendable {
    public let route: Route
    public let speedMultiplier: Double
    public let every: TimeInterval
    public let fixes: AsyncStream<Fix>

    private let continuation: AsyncStream<Fix>.Continuation
    private let lock = NSLock()
    private var index = 0
    private var paused = false
    private var track: [Fix] = []
    private var task: Task<Void, Never>?

    public init(route: Route, speedMultiplier: Double = 1, every: TimeInterval = 1) {
        self.route = route
        self.speedMultiplier = speedMultiplier
        self.every = every
        (fixes, continuation) = AsyncStream.makeStream(bufferingPolicy: .bufferingNewest(1))
    }

    deinit {
        task?.cancel()
        continuation.finish()
    }

    public var isPaused: Bool { lock.withLock { paused } }

    public func wallInterval(forRouteSeconds seconds: TimeInterval) -> TimeInterval {
        seconds / speedMultiplier
    }

    public func start() {
        lock.withLock {
            paused = false
            guard task == nil else { return }
            track = Self.track(route: route, every: every, from: Date())
            index = 0
        }
        let pace = wallInterval(forRouteSeconds: every)
        task = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if let fix = self.nextFix() { self.continuation.yield(fix) }
                try? await Task.sleep(for: .seconds(pace))
            }
        }
    }

    public func pause() { lock.withLock { paused = true } }

    /// Back to the start of the route, playing.
    public func restart() {
        lock.withLock {
            track = Self.track(route: route, every: every, from: Date())
            index = 0
            paused = false
        }
        if task == nil { start() }
    }

    private func nextFix() -> Fix? {
        lock.withLock {
            guard !paused, index < track.count else { return nil }
            defer { index += 1 }
            return track[index]
        }
    }

    /// Every fix of one drive along the route, `every` seconds of route time apart, ending at the ETA.
    public static func track(route: Route, every: TimeInterval, from start: Date) -> [Fix] {
        let line = Polyline(route.geometry)
        guard route.etaS > 0, line.length > 0 else { return [] }
        let speed = line.length / route.etaS
        var times = Array(stride(from: 0, to: route.etaS, by: every))
        times.append(route.etaS)
        return times.map { t in
            Fix(coordinate: line.point(at: t * speed), course: nil, speed: speed,
                time: start.addingTimeInterval(t))
        }
    }
}
