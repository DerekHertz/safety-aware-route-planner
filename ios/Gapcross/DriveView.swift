import GapcrossCore
import MapKit
import SwiftUI

struct DriveView: View {
    @Bindable var model: DriveModel
    @Environment(\.dismiss) private var dismiss
    @State private var camera = MapCameraPosition.automatic

    var body: some View {
        RouteMap(routes: [(model.session.route, nil, true)], position: $camera, driver: model.position)
            .ignoresSafeArea(edges: .bottom)
            .safeAreaInset(edge: .top) { banner }
            .safeAreaInset(edge: .bottom) { controls }
            .onAppear { camera = .region(model.session.route.region) }
            .onChange(of: model.position) { _, position in
                guard let position else { return }
                camera = .camera(MapCamera(centerCoordinate: position.coordinate, distance: 900))
            }
            .onDisappear { model.stop() }
    }

    @ViewBuilder private var banner: some View {
        VStack(spacing: 8) {
            if model.session.showsOffRouteBanner {
                Button {
                    Task { await model.recompute() }
                } label: {
                    Text(model.session.isRecomputing ? AppText.recomputing : AppText.offRoute)
                        .font(.headline).frame(maxWidth: .infinity).padding()
                        .background(Color.orange, in: RoundedRectangle(cornerRadius: 14))
                        .foregroundStyle(.white)
                }
            }
            switch model.session.guidance {
            case let .following(next, distance):
                HStack(spacing: 14) {
                    Image(systemName: icon(for: next)).font(.system(size: 34, weight: .bold))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(Distance.display(distance)).font(.title.bold())
                        Text(next.instruction).font(.headline)
                    }
                    Spacer()
                }
                .padding().background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            case .arrived:
                Text(AppText.arrived).font(.title2.bold()).frame(maxWidth: .infinity).padding()
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            case .offRoute, nil:
                EmptyView()
            }
        }
        .padding(.horizontal)
    }

    private var controls: some View {
        HStack {
            if model.session.phase == .driving {
                Button(model.isPausedState ? AppText.resume : AppText.pause) { model.togglePause() }
                Button(AppText.restart) { model.restart() }
            }
            Spacer()
            Text("\(AppText.estimatedDriveTime) \(DriveTime.display(model.session.route.etaS))").font(.caption)
            Spacer()
            Button(model.session.phase == .driving ? AppText.endDrive : AppText.done) {
                model.stop()
                dismiss()
            }
            .buttonStyle(.borderedProminent)
        }
        .buttonStyle(.bordered)
        .padding()
        .background(.regularMaterial)
    }

    private func icon(for step: Step) -> String {
        switch (step.maneuver._type, step.maneuver.modifier) {
        case ("arrive", _): "flag.checkered"
        case (let type, _) where type.contains("roundabout") || type == "rotary": "arrow.triangle.turn.up.right.circle"
        case (_, "left"), (_, "sharp left"): "arrow.turn.up.left"
        case (_, "right"), (_, "sharp right"): "arrow.turn.up.right"
        case (_, "slight left"): "arrow.up.left"
        case (_, "slight right"): "arrow.up.right"
        case (_, "uturn"): "arrow.uturn.down"
        default: "arrow.up"
        }
    }
}
