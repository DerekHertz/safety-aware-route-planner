"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ProjectedUnsafePoint,
  alertsAhead,
  endpointDistances,
  nextManeuver,
  projectOnRoute,
  projectUnsafePoints,
  remaining,
} from "./routeProgress";
import { LatLon, Maneuver, RouteAlternative } from "./types";

/** Perpendicular distance from the route line beyond which we consider the
 *  traveler to have actually left it, rather than GPS noise. */
const OFF_ROUTE_THRESHOLD_M = 35;
/** Consecutive over-threshold fixes required before declaring off-route —
 *  absorbs single noisy fixes instead of rerouting on every one. */
const OFF_ROUTE_HYSTERESIS_FIXES = 3;
/** Fixes noisier than this are ignored for the off-route decision (though
 *  still used to compute displayed progress). */
const MAX_ACCURACY_M = 30;
/** How far ahead along the route to surface an upcoming safety alert. */
const ALERT_LOOKAHEAD_M = 150;
/** Grace period after starting navigation, and proximity to either endpoint,
 *  during which off-route detection is suppressed — the route begins at a
 *  snapped point that can sit 20-30m from where the user is actually
 *  standing, which would otherwise read as an immediate false positive. */
const ENDPOINT_GRACE_M = 30;
const START_GRACE_MS = 15_000;

export interface RouteProgressState {
  offsetM: number;
  offRouteM: number;
  offRoute: boolean;
  remainingM: number;
  remainingS: number;
  nextManeuver: Maneuver | null;
  /** Newly-entered-lookahead alerts on this update only — each one fires
   *  exactly once per route, not on every subsequent fix. */
  alerts: ProjectedUnsafePoint[];
}

/**
 * Tracks live progress along `route` as `position` updates: how far along
 * the route the traveler is, whether they've actually left it (with
 * hysteresis, not on the first noisy fix), and one-time proximity alerts
 * for unsafe points ahead. Pass `route: null` to disable (e.g. when not
 * actively navigating) — the hook resets all internal state whenever the
 * route identity changes.
 */
export function useRouteProgress(
  route: RouteAlternative | null,
  position: LatLon | null,
  accuracy: number | null,
): RouteProgressState | null {
  const [state, setState] = useState<RouteProgressState | null>(null);
  const offRouteStreakRef = useRef(0);
  const firedRef = useRef<Set<string>>(new Set());
  const routeRef = useRef<RouteAlternative | null>(null);
  const startTsRef = useRef<number>(0);

  const projectedUnsafe = useMemo(
    () =>
      route
        ? projectUnsafePoints(route.unsafe_points, route.geometry.coordinates)
        : [],
    [route],
  );

  // Reset all tracking state when the active route changes identity (a new
  // route object from a reroute, or navigation stopping/starting).
  useEffect(() => {
    if (routeRef.current === route) return;
    routeRef.current = route;
    offRouteStreakRef.current = 0;
    firedRef.current = new Set();
    startTsRef.current = Date.now();
    setState(null);
  }, [route]);

  useEffect(() => {
    if (!route || !position) {
      setState(null);
      return;
    }
    const { offsetM, offRouteM } = projectOnRoute(
      route.geometry.coordinates,
      position,
    );
    const { remainingM, remainingS } = remaining(
      route.distance_m,
      route.eta_s,
      offsetM,
    );

    const noisy = accuracy != null && accuracy > MAX_ACCURACY_M;
    // Endpoint proximity must be measured as straight-line distance to the
    // actual start/end coordinates, NOT via `offsetM`/`remainingM`: once the
    // traveler is well off-route the nearest-point projection clamps toward an
    // endpoint, collapsing `offsetM` and making `nearEndpoint` true at exactly
    // the moment off-route detection needs to fire.
    const { startM, endM } = endpointDistances(
      route.geometry.coordinates,
      position,
    );
    const nearEndpoint = startM < ENDPOINT_GRACE_M || endM < ENDPOINT_GRACE_M;
    const withinStartGrace = Date.now() - startTsRef.current < START_GRACE_MS;
    const suppressed = noisy || nearEndpoint || withinStartGrace;

    if (suppressed) {
      offRouteStreakRef.current = 0;
    } else {
      offRouteStreakRef.current =
        offRouteM > OFF_ROUTE_THRESHOLD_M ? offRouteStreakRef.current + 1 : 0;
    }
    const offRoute = offRouteStreakRef.current >= OFF_ROUTE_HYSTERESIS_FIXES;

    const upcoming = nextManeuver(route.maneuvers, offsetM);

    const alerts = alertsAhead(
      projectedUnsafe,
      offsetM,
      ALERT_LOOKAHEAD_M,
    ).filter((a) => {
      const key = `${route.kind}:${a.point.type}:${a.offsetM}`;
      if (firedRef.current.has(key)) return false;
      firedRef.current.add(key);
      return true;
    });

    setState({
      offsetM,
      offRouteM,
      offRoute,
      remainingM,
      remainingS,
      nextManeuver: upcoming,
      alerts,
    });
  }, [route, position, accuracy, projectedUnsafe]);

  return state;
}
