import GapcrossCore
import MapKit
import SwiftUI

/// Both routes and the markers on every crossing of the routes shown.
struct RouteMap: View {
    let routes: [(route: Route, outcome: Comparison.OutcomePayload?, selected: Bool)]
    var position: Binding<MapCameraPosition>
    var driver: LatLon?
    var onMarker: (CrossingMarker) -> Void = { _ in }

    var body: some View {
        Map(position: position) {
            ForEach(Array(routes.enumerated()), id: \.offset) { _, item in
                MapPolyline(coordinates: item.route.coordinates)
                    .stroke(item.selected ? Color.accentColor : Color.gray.opacity(0.6),
                            style: StrokeStyle(lineWidth: item.selected ? 7 : 5, lineCap: .round, lineJoin: .round))
            }
            ForEach(Array(routes.enumerated()), id: \.offset) { _, item in
                ForEach(CrossingsDisplay(route: item.route, outcome: item.outcome).markers) { marker in
                    Annotation(marker.mainStreet, coordinate: marker.location.coordinate, anchor: .center) {
                        MarkerBadge(symbol: marker.symbol, dimmed: !item.selected)
                            .onTapGesture { onMarker(marker) }
                    }
                    .annotationTitles(.hidden)
                }
            }
            if let driver {
                Annotation("", coordinate: driver.coordinate, anchor: .center) {
                    Circle().fill(Color.blue).stroke(.white, lineWidth: 3).frame(width: 20, height: 20)
                        .shadow(radius: 2)
                }
            }
        }
        .mapStyle(.standard(pointsOfInterest: .excludingAll))
    }
}

/// `!` flagged, `?` unknown, a quiet check for unflagged.
struct MarkerBadge: View {
    let symbol: MarkerSymbol
    var dimmed = false

    var body: some View {
        Group {
            switch symbol {
            case .flagged:
                Text("!").font(.system(size: 15, weight: .heavy)).foregroundStyle(.white)
                    .frame(width: 26, height: 26).background(Circle().fill(Color.orange))
            case .unknown:
                Text("?").font(.system(size: 15, weight: .heavy)).foregroundStyle(.white)
                    .frame(width: 26, height: 26).background(Circle().fill(Color.purple))
            case .unflagged:
                Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)).foregroundStyle(.secondary)
                    .frame(width: 16, height: 16).background(Circle().fill(.background))
                    .overlay(Circle().stroke(Color.secondary.opacity(0.5)))
            }
        }
        .shadow(radius: symbol == .unflagged ? 0 : 1.5)
        .opacity(dimmed ? 0.55 : 1)
        .contentShape(Rectangle().size(width: 44, height: 44))
    }
}
