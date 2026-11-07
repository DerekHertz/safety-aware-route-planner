import CoreLocation
import GapcrossCore
import MapKit

extension LatLon {
    var coordinate: CLLocationCoordinate2D { CLLocationCoordinate2D(latitude: lat, longitude: lon) }
}

extension Route {
    var coordinates: [CLLocationCoordinate2D] { geometry.map(\.coordinate) }

    /// A region that frames the whole route with some margin.
    var region: MKCoordinateRegion {
        let lats = geometry.map(\.lat), lons = geometry.map(\.lon)
        guard let s = lats.min(), let n = lats.max(), let w = lons.min(), let e = lons.max() else {
            return MKCoordinateRegion()
        }
        return MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: (s + n) / 2, longitude: (w + e) / 2),
            span: MKCoordinateSpan(latitudeDelta: (n - s) * 1.3 + 0.002, longitudeDelta: (e - w) * 1.3 + 0.002))
    }

    var labelText: String {
        switch label {
        case .example: AppText.exampleRoute
        case .provider: AppText.providerRoute
        case .rerouted: AppText.reroutedRoute
        }
    }

    var sourceText: String { source == .recorded ? AppText.recordedData : AppText.liveData }
}

/// MapKit has its own `Marker`.
typealias CrossingMarker = GapcrossCore.Marker
