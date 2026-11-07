import Foundation
import GapcrossCore
import Observation

enum Choice: Hashable { case `default`, alternative }

/// Flow C, planning: choose a trip, draw its default route at once, and with avoidance on, ask for
/// the comparison and draw the alternative. Holds no assessment logic.
@Observable
final class Planner {
    /// Overridable with the launch argument `-GapcrossServiceURL http://host:8000`.
    static var serviceURL: URL {
        UserDefaults.standard.string(forKey: "GapcrossServiceURL").flatMap(URL.init(string:))
            ?? URL(string: "http://localhost:8000")!
    }

    let service: any GapcrossService
    private(set) var trips: [Trip] = []
    private(set) var trip: Trip?
    private(set) var defaultRoute: Route?
    private(set) var comparison: Comparison?
    private(set) var isLoading = false
    private(set) var isComparing = false
    private(set) var problem: String?
    var avoid = false { didSet { if avoid != oldValue { Task { await avoidChanged() } } } }
    var choice = Choice.default

    init(service: any GapcrossService) { self.service = service }

    var chosenRoute: Route? {
        choice == .alternative ? comparison?.alternative ?? defaultRoute : defaultRoute
    }

    /// The default route shown beside the comparison: the comparison's copy once it has arrived.
    var shownDefault: Route? { comparison?._default ?? defaultRoute }

    func load() async {
        problem = nil
        do {
            trips = try await service.trips()
            if trip == nil, let first = trips.first { await choose(first) }
        } catch {
            problem = Self.message(for: error)
        }
    }

    func choose(_ trip: Trip) async {
        self.trip = trip
        defaultRoute = nil
        comparison = nil
        choice = .default
        isLoading = true
        problem = nil
        defer { isLoading = false }
        do {
            defaultRoute = try await service.defaultRoute(tripID: trip.id)
            if avoid { await compare() }
        } catch {
            problem = Self.message(for: error)
        }
    }

    func retry() async {
        if let trip { await choose(trip) } else { await load() }
    }

    private func avoidChanged() async {
        if avoid {
            await compare()
        } else {
            comparison = nil
            choice = .default
        }
    }

    private func compare() async {
        guard let trip else { return }
        isComparing = true
        defer { isComparing = false }
        do {
            let result = try await service.comparison(tripID: trip.id)
            if avoid, self.trip == trip { comparison = result }
        } catch {
            problem = Self.message(for: error)
        }
    }

    static func message(for error: Error) -> String {
        (error as? ServiceError) == .routeUnavailable ? AppText.routeUnavailable : AppText.somethingWentWrong
    }
}
