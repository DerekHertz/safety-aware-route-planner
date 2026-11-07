import GapcrossCore
import SwiftUI

@main
struct GapcrossApp: App {
    @State private var planner = Planner(service: LiveService(baseURL: Planner.serviceURL))

    var body: some Scene {
        WindowGroup {
            PlanView(planner: planner)
        }
    }
}
