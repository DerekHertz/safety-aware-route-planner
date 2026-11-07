import AVFoundation
import Foundation
import GapcrossCore
import Observation

/// A simulated drive: the feed, the follower and the session, plus spoken prompts.
@Observable
final class DriveModel: Identifiable {
    let id = UUID()
    let session: DriveSession
    private(set) var position: LatLon?
    private(set) var feed: SimulatedFeed
    private var task: Task<Void, Never>?
    private var announcedStep: Step?
    private var announcedNear = false
    private let voice = AVSpeechSynthesizer()

    init(tripID: String, route: Route, speed: Double, service: any GapcrossService) {
        session = DriveSession(tripID: tripID, route: route, feed: .simulated, service: service, departAt: Date())
        feed = SimulatedFeed(route: route, speedMultiplier: speed)
        follow()
    }

    var isPaused: Bool { feed.isPaused }
    var isPausedState = false

    func togglePause() {
        if feed.isPaused { feed.start() } else { feed.pause() }
        isPausedState = feed.isPaused
    }

    func restart() {
        task?.cancel()
        feed = SimulatedFeed(route: session.route, speedMultiplier: feed.speedMultiplier)
        announcedStep = nil
        follow()
        isPausedState = false
    }

    func recompute() async {
        guard let position else { return }
        do {
            try await session.recomputeTapped(at: position)
            task?.cancel()
            feed = SimulatedFeed(route: session.route, speedMultiplier: feed.speedMultiplier)
            announcedStep = nil
            follow()
        } catch {}
    }

    func stop() {
        task?.cancel()
        voice.stopSpeaking(at: .immediate)
    }

    private func follow() {
        let feed = self.feed
        var follower = Follower(route: session.route)
        task = Task { [weak self] in
            feed.start()
            for await fix in feed.fixes {
                guard let self, !Task.isCancelled else { return }
                self.position = fix.coordinate
                let state = follower.update(fix)
                self.session.receive(state)
                self.speak(state)
                if state == .arrived { return }
            }
        }
    }

    /// One prompt when a step becomes next, and one as it comes up, in miles and feet.
    private func speak(_ state: GuidanceState) {
        switch state {
        case let .following(next, distance):
            if next != announcedStep {
                announcedStep = next
                announcedNear = distance < 120
                say(distance < 120 ? next.instruction : "\(AppText.then(Distance.spoken(distance))), \(next.instruction)")
            } else if !announcedNear, distance < 120 {
                announcedNear = true
                say(next.instruction)
            }
        case .arrived:
            say(AppText.arrived)
        case .offRoute:
            break
        }
    }

    private func say(_ text: String) {
        voice.stopSpeaking(at: .word)
        voice.speak(AVSpeechUtterance(string: text))
    }
}
