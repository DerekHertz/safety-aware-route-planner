import Foundation
import GapcrossAPI
@testable import GapcrossCore

enum Recorded {
    static func decode<T: Decodable>(_ name: String, as type: T.Type = T.self) throws -> T {
        let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures")!
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try decoder.decode(T.self, from: Data(contentsOf: url))
    }

    static var defaultRoute: Route { get throws { try decode("default") } }
    static var comparison: Comparison { get throws { try decode("comparison") } }
}

/// Meters east and north of a fixed origin, so expected distances can be worked out by hand.
func point(east: Double, north: Double) -> LatLon {
    let origin = (lat: 37.86, lon: -122.27)
    let k = 111_320.0
    return LatLon(lat: origin.lat + north / k, lon: origin.lon + east / (k * cos(origin.lat * .pi / 180)))
}

func step(_ instruction: String, along: Double, at location: LatLon, type: String = "turn") -> Step {
    Step(alongM: along, distanceM: 0, instruction: instruction, location: location,
         maneuver: .init(modifier: nil, _type: type), street: "")
}

/// 1 km east, then 500 m north: depart, one turn, arrive.
func lRoute(label: Route.LabelPayload = .example, crossings: [Crossing] = [], checked: Bool = true) -> Route {
    let corner = point(east: 1000, north: 0)
    let end = point(east: 1000, north: 500)
    return Route(
        crossings: crossings, crossingsChecked: checked, distanceM: 1500, etaS: 150,
        geometry: [point(east: 0, north: 0), corner, end], label: label, source: .recorded,
        steps: [step("Start", along: 0, at: point(east: 0, north: 0), type: "depart"),
                step("Turn left onto North Street", along: 1000, at: corner),
                step("Arrive at your destination", along: 1500, at: end, type: "arrive")])
}

func crossing(_ key: String, _ status: Assessment.StatusPayload, along: Double = 500) -> Crossing {
    Crossing(alongM: along, approachBearing: 90,
             assessment: .init(evidence: [], explanation: "Because.", status: status),
             key: key, kind: .straight, location: point(east: along, north: 0), mainStreet: "Main Street")
}

func fix(_ p: LatLon, at seconds: Double) -> Fix {
    Fix(coordinate: p, course: nil, speed: nil, time: Date(timeIntervalSince1970: seconds))
}
