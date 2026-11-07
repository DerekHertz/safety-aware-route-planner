import GapcrossCore
import MapKit
import SwiftUI

struct PlanView: View {
    @Bindable var planner: Planner
    @State private var camera = MapCameraPosition.automatic
    @State private var marker: CrossingMarker?
    @State private var drive: DriveModel?
    @State private var speed = 4.0

    var body: some View {
        RouteMap(routes: shownRoutes, position: $camera, onMarker: { marker = $0 })
            .safeAreaInset(edge: .bottom) { panel }
            .task { await planner.load() }
            .onChange(of: planner.defaultRoute?.geometry) { _, _ in frame() }
            .sheet(item: $marker) { MarkerDetailView(marker: $0).presentationDetents([.medium, .large]) }
            .fullScreenCover(item: $drive) { DriveView(model: $0) }
    }

    private var shownRoutes: [(route: Route, outcome: Comparison.OutcomePayload?, selected: Bool)] {
        let outcome = planner.comparison?.outcome
        var routes: [(Route, Comparison.OutcomePayload?, Bool)] = []
        if let route = planner.shownDefault { routes.append((route, outcome, planner.choice == .default)) }
        if let route = planner.comparison?.alternative {
            routes.append((route, nil, planner.choice == .alternative))
        }
        return routes.sorted { !$0.2 && $1.2 }  // the selected route draws on top
    }

    private func frame() {
        if let route = planner.defaultRoute { camera = .region(route.region) }
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 12) {
            if planner.trips.count > 1 {
                Picker(AppText.chooseTrip, selection: Binding(
                    get: { planner.trip?.id ?? "" },
                    set: { id in if let t = planner.trips.first(where: { $0.id == id }) { Task { await planner.choose(t) } } }
                )) { ForEach(planner.trips, id: \.id) { Text($0.name).tag($0.id) } }
            } else if let trip = planner.trip {
                Text(trip.name).font(.headline)
            }

            if let problem = planner.problem {
                HStack {
                    Text(problem).foregroundStyle(.secondary)
                    Spacer()
                    Button(AppText.retry) { Task { await planner.retry() } }.buttonStyle(.bordered)
                }
            }

            if let route = planner.shownDefault {
                Toggle(AppText.avoidToggle, isOn: $planner.avoid)
                RouteCard(title: AppText.defaultRoute, route: route, outcome: planner.comparison?.outcome,
                          selected: planner.choice == .default) { planner.choice = .default }
                if planner.isComparing {
                    HStack(spacing: 8) { ProgressView(); Text(AppText.searching).foregroundStyle(.secondary) }
                }
                if let comparison = planner.comparison {
                    if let alternative = comparison.alternative {
                        RouteCard(title: AppText.alternative, route: alternative, outcome: nil,
                                  selected: planner.choice == .alternative) { planner.choice = .alternative }
                    }
                    Text(comparison.summary).font(.subheadline)
                }
                startRow
            } else if planner.isLoading {
                ProgressView().frame(maxWidth: .infinity)
            }
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
        .padding(.horizontal, 8)
    }

    private var startRow: some View {
        HStack {
            Menu {
                Picker(AppText.playbackSpeed, selection: $speed) {
                    ForEach([1.0, 4, 16, 32], id: \.self) { Text("\(Int($0))×").tag($0) }
                }
            } label: {
                Label("\(AppText.simulatedDrive) · \(Int(speed))×", systemImage: "play.circle")
                    .font(.subheadline)
            }
            Spacer()
            Button(AppText.start) {
                guard let trip = planner.trip, let route = planner.chosenRoute else { return }
                drive = DriveModel(tripID: trip.id, route: route, speed: speed, service: planner.service)
            }
            .buttonStyle(.borderedProminent)
            .disabled(planner.chosenRoute == nil)
        }
    }
}

struct RouteCard: View {
    let title: String
    let route: Route
    let outcome: Comparison.OutcomePayload?
    let selected: Bool
    let select: () -> Void

    var body: some View {
        Button(action: select) {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(title).font(.subheadline.bold())
                    Text("\(route.labelText) · \(route.sourceText)").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(selected ? Color.accentColor : .secondary)
                }
                Text("\(AppText.estimatedDriveTime) \(DriveTime.display(route.etaS)) · \(Distance.display(route.distanceM)) · \(AppText.trafficUnknown)")
                    .font(.caption)
                crossings
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12).stroke(selected ? Color.accentColor : .secondary.opacity(0.3)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder private var crossings: some View {
        switch CrossingsDisplay(route: route, outcome: outcome) {
        case .notChecked: Text(AppText.crossingsNotChecked).font(.caption.bold())
        case .notAssessed: Text(AppText.crossingsNotAssessed).font(.caption.bold())
        case let .assessed(markers, flagged, unknown):
            if markers.isEmpty {
                Text(AppText.noCrossings).font(.caption)
            } else {
                HStack(spacing: 6) {
                    MarkerBadge(symbol: .flagged).scaleEffect(0.7)
                    Text(AppText.counts(flagged: flagged, unknown: unknown)).font(.caption.bold())
                }
            }
        }
    }
}
