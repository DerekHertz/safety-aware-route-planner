import { RouteProgressState } from "./useRouteProgress";

/** Distance-to-destination inside which we declare arrival. */
export const ARRIVAL_RADIUS_M = 25;

/** The live-nav session lifecycle. A session is `navigating` until the traveler
 *  reaches the destination, then `arrived` — a terminal state that stops
 *  rerouting (ADR-0008: reroute always targeted the original destination, with
 *  no "you have arrived" state; this adds one). */
export type NavPhase = "navigating" | "arrived";

/**
 * Whether the traveler has arrived, measured as **great-circle distance to the
 * destination coordinate** — deliberately NOT the route projection's
 * `remainingM`. `remainingM` is `distance - offsetM`, and `offsetM` clamps to
 * the route's end vertex once the traveler is far off-route laterally, which
 * would declare a false arrival hundreds of meters from the destination. A
 * direct distance to the destination cannot be fooled that way.
 */
export function hasArrived(distToDestM: number): boolean {
  return distToDestM < ARRIVAL_RADIUS_M;
}

/**
 * Whether the session should reroute on this progress update: only while
 * actively navigating (not arrived), when the traveler has left the route, and
 * with no reroute already in flight. Pure so it can be unit-tested without
 * React, GPS, or the network.
 */
export function shouldReroute(
  phase: NavPhase,
  progress: RouteProgressState | null,
  rerouting: boolean,
): boolean {
  return phase === "navigating" && !!progress?.offRoute && !rerouting;
}
