"use client";

import { useEffect, useRef, useState } from "react";
import { fetchReroute } from "./api";
import { NavPhase, hasArrived, shouldReroute } from "./navigation";
import { LatLon, RouteAlternative } from "./types";
import { distanceMeters } from "./useGeolocation";
import { RouteProgressState, useRouteProgress } from "./useRouteProgress";

export interface NavigationState {
  /** The route currently being FOLLOWED — the initial choice, replaced in place
   *  by each successful reroute. Null when no session is active. */
  route: RouteAlternative | null;
  progress: RouteProgressState | null;
  phase: NavPhase;
  rerouting: boolean;
  rerouteError: string | null;
}

const IDLE: NavigationState = {
  route: null,
  progress: null,
  phase: "navigating",
  rerouting: false,
  rerouteError: null,
};

/**
 * Owns a live-navigation session so the planner doesn't have to. Seed it with
 * `initialRoute` (the chosen alternative) to start; pass `null` to stop. While
 * active it tracks progress along the *followed* route and, when the traveler
 * leaves it, replans via `POST /reroute` carrying that route's `preference` —
 * so the replacement is a single artifact at the SAME safety level (ADR-0008),
 * swapped in place. Reaching the destination ends the session in the `arrived`
 * phase. The planner's own state (origin/selected/routes) is untouched.
 *
 * `rerouting` is DERIVED from progress rather than stored, so the reroute effect
 * performs no synchronous setState (see the `set-state-in-effect` ratchet in
 * eslint.config.mjs): the only state it writes — the replacement route and any
 * error — happens inside the fetch's async callbacks. Arrival IS latched in
 * state, but from a render-time adjustment (not an effect), so it stays sticky
 * once reached without tripping the same rule.
 */
export function useNavigation(
  initialRoute: RouteAlternative | null,
  destination: LatLon | null,
  position: LatLon | null,
  accuracy: number | null,
): NavigationState {
  const [followedRoute, setFollowedRoute] = useState<RouteAlternative | null>(
    null,
  );
  const [rerouteError, setRerouteError] = useState<string | null>(null);
  // Latched once the traveler reaches the destination; sticky so overshooting
  // past it doesn't flip back to navigating.
  const [arrived, setArrived] = useState(false);
  // Guards against firing a second reroute while one is in flight; a ref (not
  // state) so it neither re-renders nor needs a synchronous setState.
  const reroutingRef = useRef(false);
  const sessionRef = useRef<RouteAlternative | null>(null);

  // Start/stop a session when `initialRoute` identity changes. Seeding the
  // followed route here (not from render) keeps the session's route independent
  // of later planner changes — the same reset-on-identity pattern as
  // useRouteProgress. The identity guard keeps these setState calls off the
  // effect's unconditional path.
  useEffect(() => {
    if (sessionRef.current === initialRoute) return;
    sessionRef.current = initialRoute;
    setFollowedRoute(initialRoute);
    setArrived(false);
    reroutingRef.current = false;
    setRerouteError(null);
  }, [initialRoute]);

  const progress = useRouteProgress(followedRoute, position, accuracy);

  // Arrival = within ARRIVAL_RADIUS_M of the destination by great-circle
  // distance, latched (render-time adjustment, not an effect — the pattern the
  // React docs sanction and the `set-state-in-effect` rule allows). Great-circle
  // distance rather than `progress.remainingM`, whose clamped projection can
  // read 0 while hundreds of meters off-route near the end.
  const distToDestM =
    followedRoute && position && destination
      ? distanceMeters(position, destination)
      : Infinity;
  if (!arrived && hasArrived(distToDestM)) setArrived(true);
  const phase: NavPhase = arrived ? "arrived" : "navigating";

  useEffect(() => {
    if (!followedRoute || !position || !destination) return;
    if (!shouldReroute(phase, progress, reroutingRef.current)) {
      return;
    }
    reroutingRef.current = true;
    let cancelled = false;
    fetchReroute(position, destination, followedRoute.preference)
      .then((resp) => {
        if (cancelled) return;
        // In-place, same-level replacement. Changing route identity resets
        // useRouteProgress's off-route streak and start-grace, so there is no
        // immediate re-trigger.
        setFollowedRoute(resp.route);
        setRerouteError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // Keep following the old route; surface the failure. `reroutingRef`
        // clears below, so the next off-route fix retries.
        setRerouteError(e instanceof Error ? e.message : "reroute failed");
      })
      .finally(() => {
        reroutingRef.current = false;
      });
    return () => {
      cancelled = true;
    };
  }, [followedRoute, progress, phase, position, destination]);

  if (!followedRoute) return IDLE;
  // While off-route (and not yet arrived) a reroute is imminent or in flight;
  // derived from progress so the "recalculating" HUD re-renders on each fix
  // without a dedicated flag.
  const rerouting = phase === "navigating" && !!progress?.offRoute;
  return { route: followedRoute, progress, phase, rerouting, rerouteError };
}
