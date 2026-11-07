import Foundation

/// The wire carries meters and seconds; the app shows miles, and feet under about 0.1 mi.
public enum Distance {
    static let metersPerMile = 1609.344
    static let feetPerMeter = 3.28084

    public static func display(_ meters: Double) -> String {
        switch measure(meters) {
        case let .feet(n): "\(n) ft"
        case let .miles(text, _): "\(text) mi"
        }
    }

    public static func spoken(_ meters: Double) -> String {
        switch measure(meters) {
        case let .feet(n): "\(n) feet"
        case let .miles(text, one): one ? "1 mile" : "\(text) miles"
        }
    }

    private enum Measure { case feet(Int), miles(String, one: Bool) }

    private static func measure(_ meters: Double) -> Measure {
        let miles = meters / metersPerMile
        if miles < 0.1 {
            return .feet(max(50, Int((meters * feetPerMeter / 50).rounded()) * 50))
        }
        let rounded = miles < 10 ? (miles * 10).rounded() / 10 : miles.rounded()
        let text = rounded == rounded.rounded() ? String(Int(rounded)) : String(format: "%.1f", rounded)
        return .miles(text, one: rounded == 1)
    }
}

public enum DriveTime {
    public static func display(_ seconds: Double) -> String {
        let minutes = max(1, Int((seconds / 60).rounded()))
        guard minutes >= 60 else { return "\(minutes) min" }
        let rest = minutes % 60
        return rest == 0 ? "\(minutes / 60) hr" : "\(minutes / 60) hr \(rest) min"
    }
}
