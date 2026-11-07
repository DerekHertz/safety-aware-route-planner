import Foundation

/// A route's geometry measured in meters, with the same flat-earth approximation as the service.
struct Polyline: Sendable {
    let points: [LatLon]
    let cumulative: [Double]
    var length: Double { cumulative.last ?? 0 }

    init(_ points: [LatLon]) {
        var kept: [LatLon] = []
        for p in points where kept.last != p { kept.append(p) }
        self.points = kept
        var total = [0.0]
        for (a, b) in zip(kept, kept.dropFirst()) { total.append(total.last! + Self.distance(a, b)) }
        cumulative = total
    }

    /// The distance along the line to the point on it closest to `p`, and how far `p` is from it.
    /// Only segments overlapping `range` are considered when one is given.
    func project(_ p: LatLon, within range: ClosedRange<Double>? = nil) -> (along: Double, offset: Double) {
        var best = (along: 0.0, offset: Double.infinity)
        for k in 0..<max(points.count - 1, 0) {
            if let range, cumulative[k + 1] < range.lowerBound || cumulative[k] > range.upperBound { continue }
            let (t, d) = Self.projectOnSegment(p, points[k], points[k + 1])
            if d < best.offset {
                best = (cumulative[k] + t * (cumulative[k + 1] - cumulative[k]), d)
            }
        }
        return best
    }

    func point(at along: Double) -> LatLon {
        guard let first = points.first else { return LatLon(lat: 0, lon: 0) }
        if along <= 0 { return first }
        for k in 0..<max(points.count - 1, 0) where along <= cumulative[k + 1] && cumulative[k + 1] > cumulative[k] {
            let t = (along - cumulative[k]) / (cumulative[k + 1] - cumulative[k])
            let a = points[k], b = points[k + 1]
            return LatLon(lat: a.lat + t * (b.lat - a.lat), lon: a.lon + t * (b.lon - a.lon))
        }
        return points.last!
    }

    static func distance(_ a: LatLon, _ b: LatLon) -> Double {
        let (x, y) = xy(b, origin: a)
        return (x * x + y * y).squareRoot()
    }

    private static func xy(_ p: LatLon, origin: LatLon) -> (Double, Double) {
        let k = 111_320.0
        return ((p.lon - origin.lon) * k * cos(origin.lat * .pi / 180), (p.lat - origin.lat) * k)
    }

    private static func projectOnSegment(_ p: LatLon, _ a: LatLon, _ b: LatLon) -> (Double, Double) {
        let (bx, by) = xy(b, origin: a)
        let (px, py) = xy(p, origin: a)
        let length2 = bx * bx + by * by
        let t = length2 == 0 ? 0 : min(1, max(0, (px * bx + py * by) / length2))
        let dx = px - t * bx, dy = py - t * by
        return (t, (dx * dx + dy * dy).squareRoot())
    }
}
