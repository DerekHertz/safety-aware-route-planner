import { RouteProgressState } from "./useRouteProgress";

/** Distance-to-destination inside which we declare arrival. `remainingM` is the
 *  proportional remaining-distance estimate from `useRouteProgress`; once it
 *  drops below this the traveler is close enough that a reroute would be noise. */
export const ARRIVAL_RADIUS_M = 25;

/** The live-nav session lifecycle. A session is `navigating` until the traveler
 *  reaches the destination, then `arrived` — a terminal state that stops
 *  rerouting (ADR-0008: reroute always targeted the original destination, with
 *  no "you have arrived" state; this adds one). */
export type NavPhase = "navigating" | "arrived";

/** What the navigation session should do on the current progress update.
 *  - `"arrive"`: close enough to the destination to end the session.
 *  - `"reroute"`: the traveler has left the route; replan at the carried level.
 *  - `"none"`: keep following the current route. */
export type NavAction = "reroute" | "arrive" | "none";

/**
 * Pure decision for a live-nav update — extracted from the hook so it is
 * testable without React, GPS, or the network. Arrival is checked first: once
 * within {@link ARRIVAL_RADIUS_M} of the destination we end the session rather
 * than reroute, even if the fix reads as off-route. A session already in the
 * `arrived` phase, or one with a reroute already in flight, takes no action.
 */
export function decideNavAction(
  phase: NavPhase,
  progress: RouteProgressState | null,
  rerouting: boolean,
): NavAction {
  if (!progress) return "none";
  if (progress.remainingM < ARRIVAL_RADIUS_M) {
    return phase === "arrived" ? "none" : "arrive";
  }
  if (phase === "navigating" && progress.offRoute && !rerouting) {
    return "reroute";
  }
  return "none";
}
