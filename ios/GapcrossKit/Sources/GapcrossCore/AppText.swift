/// Every string the app shows or speaks, in one place so rule 14 can test them all: the app never
/// says a route is safe. Terms follow CONTEXT.md.
public enum AppText {
    public static let appTitle = "Gap crossings"
    public static let chooseTrip = "Choose a trip"
    public static let avoidToggle = "Avoid gap crossings"
    public static let avoidHint = "Look for a route that crosses busy streets where traffic stops for you, within a small added time."
    public static let defaultRoute = "Default route"
    public static let alternative = "Alternative"
    public static let exampleRoute = "Example route"
    public static let providerRoute = "Routing provider"
    public static let reroutedRoute = "Rerouted"
    public static let recordedData = "Recorded data"
    public static let liveData = "OSRM"
    public static let estimatedDriveTime = "Estimated drive time"
    public static let trafficUnknown = "Traffic unknown"
    public static let crossingsNotChecked = "Crossings couldn't be checked"
    public static let crossingsNotAssessed = "Rerouted: crossings not checked"
    public static let noCrossings = "No main-street crossings on this route"
    public static let searching = "Looking for an alternative…"
    public static let routeUnavailable = "Route couldn't be obtained."
    public static let somethingWentWrong = "Something went wrong."
    public static let retry = "Retry"
    public static let flagged = "Flagged: you wait for a gap"
    public static let unknown = "Unknown"
    public static let unflagged = "Unflagged: traffic is controlled"
    public static let evidence = "Evidence"
    public static let lead = "Lead, not evidence"
    public static let start = "Start"
    public static let pause = "Pause"
    public static let resume = "Resume"
    public static let restart = "Restart"
    public static let endDrive = "End drive"
    public static let offRoute = "You're off the route. Tap to recompute."
    public static let recomputing = "Recomputing…"
    public static let arrived = "You have arrived"
    public static let reportQuestion = "Did this crossing work the way the app described it?"
    public static let asDescribed = "As described"
    public static let notAsDescribed = "Not as described"
    public static let notSure = "Not sure"
    public static let send = "Send"
    public static let skip = "Skip"
    public static let done = "Done"
    public static let simulatedDrive = "Simulated drive"
    public static let playbackSpeed = "Playback speed"

    public static func counts(flagged: Int, unknown: Int) -> String {
        "\(flagged) flagged · \(unknown) unknown"
    }

    public static func then(_ distance: String) -> String { "In \(distance)" }

    public static var all: [String] {
        [appTitle, chooseTrip, avoidToggle, avoidHint, defaultRoute, alternative, exampleRoute,
         providerRoute, reroutedRoute, recordedData, liveData, estimatedDriveTime, trafficUnknown,
         crossingsNotChecked, crossingsNotAssessed, noCrossings, searching, routeUnavailable,
         somethingWentWrong, retry, flagged, unknown, unflagged, evidence, lead, start, pause, resume,
         restart, endDrive, offRoute, recomputing, arrived, reportQuestion, asDescribed, notAsDescribed,
         notSure, send, skip, done, simulatedDrive, playbackSpeed,
         counts(flagged: 2, unknown: 1), then("300 ft")]
    }
}
