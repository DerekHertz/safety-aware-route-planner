/// What the app may show about a route's crossings. Counts are derived here from `crossings`;
/// the service never sends them, so marker counts equal assessment counts by construction.
public enum CrossingsDisplay: Equatable, Sendable {
    /// The check could not run: "Crossings couldn't be checked", no markers, no counts (rule 2).
    case notChecked
    /// A rerouted route is not assessed: no markers and no claims (rule 11).
    case notAssessed
    case assessed(markers: [Marker], flagged: Int, unknown: Int)

    public init(route: Route, outcome: Comparison.OutcomePayload? = nil) {
        if route.label == .rerouted {
            self = .notAssessed
        } else if !route.crossingsChecked || outcome == .checkFailed
                    || route.crossings.contains(where: { $0.assessment.status == .checkFailed }) {
            self = .notChecked
        } else {
            let markers = route.crossings.compactMap(Marker.init)
            self = .assessed(markers: markers,
                             flagged: markers.filter { $0.symbol == .flagged }.count,
                             unknown: markers.filter { $0.symbol == .unknown }.count)
        }
    }

    public var markers: [Marker] {
        if case let .assessed(markers, _, _) = self { return markers }
        return []
    }
}

public enum MarkerSymbol: Sendable { case flagged, unknown, unflagged }

public struct Marker: Equatable, Sendable, Identifiable {
    public let key: String
    public let symbol: MarkerSymbol
    public let mainStreet: String
    public let location: LatLon
    public let explanation: String
    public let evidence: [Evidence]

    public var id: String { key }

    init?(_ crossing: Crossing) {
        switch crossing.assessment.status {
        case .flagged: symbol = .flagged
        case .unknown: symbol = .unknown
        case .unflagged: symbol = .unflagged
        case .checkFailed: return nil
        }
        key = crossing.key
        mainStreet = crossing.mainStreet
        location = crossing.location
        explanation = crossing.assessment.explanation
        evidence = crossing.assessment.evidence
    }
}
