// Pure geometry/progress helpers for live GPS navigation.
//
// No React, no side effects — these are plain functions over route geometry
// so they can be unit-tested without a DOM and reused from any UI layer.

import nearestPointOnLine from "@turf/nearest-point-on-line";
import { lineString, point } from "@turf/helpers";
import type { Maneuver } from "./types";

export type { Maneuver };

const EARTH_RADIUS_M = 6_371_000;

function haversineM(
  [lon1, lat1]: [number, number],
  [lon2, lat2]: [number, number],
): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(s));
}

/**
 * Resample a route's LineString into evenly-spaced points, roughly every
 * `stepM` meters, via simple linear interpolation per segment (mirrors
 * `_edge_coords` in `pyref/geometry.py`). Used to drive a simulated GPS
 * track along a real route (see useGeolocation's dev-only mock track) —
 * watchPosition never delivers fixes this dense, but a smoother synthetic
 * track makes the camera/heading behavior easier to eyeball.
 */
export function densifyRoute(
  coordinates: [number, number][],
  stepM = 10,
): { lon: number; lat: number }[] {
  if (coordinates.length < 2) {
    return coordinates.map(([lon, lat]) => ({ lon, lat }));
  }
  const out: { lon: number; lat: number }[] = [];
  for (let i = 0; i < coordinates.length - 1; i++) {
    const [lon1, lat1] = coordinates[i];
    const [lon2, lat2] = coordinates[i + 1];
    const segM = haversineM(coordinates[i], coordinates[i + 1]);
    const steps = Math.max(1, Math.round(segM / stepM));
    for (let s = 0; s < steps; s++) {
      const t = s / steps;
      out.push({
        lon: lon1 + t * (lon2 - lon1),
        lat: lat1 + t * (lat2 - lat1),
      });
    }
  }
  const [lastLon, lastLat] = coordinates[coordinates.length - 1];
  out.push({ lon: lastLon, lat: lastLat });
  return out;
}

export interface UnsafePointLike {
  lon: number;
  lat: number;
  type: string;
}

export interface ProjectedPoint {
  snapped: { lon: number; lat: number };
  /** Distance along the route, in meters, of the snapped point. */
  offsetM: number;
  /** Distance from the raw position to the route, in meters. */
  offRouteM: number;
}

/**
 * Snap a raw position onto the route line, returning how far along the
 * route (in meters) the snapped point sits and how far off the route the
 * raw position was.
 */
export function projectOnRoute(
  coordinates: [number, number][],
  pos: { lon: number; lat: number },
): ProjectedPoint {
  const line = lineString(coordinates);
  const pt = point([pos.lon, pos.lat]);
  const result = nearestPointOnLine(line, pt, { units: "meters" });
  const [lon, lat] = result.geometry.coordinates;
  return {
    snapped: { lon, lat },
    offsetM: result.properties.location ?? 0,
    offRouteM: result.properties.dist ?? 0,
  };
}

/**
 * Straight-line distance, in meters, from a raw position to the route's first
 * and last vertices. Used to decide endpoint proximity for the off-route
 * grace: unlike the projected `offsetM`, this cannot be fooled by a
 * nearest-point projection that clamps toward an endpoint when the traveler is
 * far off-route — which would otherwise suppress off-route detection at exactly
 * the moment it should fire.
 */
export function endpointDistances(
  coordinates: [number, number][],
  pos: { lon: number; lat: number },
): { startM: number; endM: number } {
  if (coordinates.length === 0) {
    return { startM: Infinity, endM: Infinity };
  }
  const p: [number, number] = [pos.lon, pos.lat];
  return {
    startM: haversineM(coordinates[0], p),
    endM: haversineM(coordinates[coordinates.length - 1], p),
  };
}

export interface RemainingEstimate {
  remainingM: number;
  remainingS: number;
}

/**
 * Proportional remaining-distance / remaining-time estimate based on how
 * far along the route (`offsetM`) the traveler currently is.
 */
export function remaining(
  distanceM: number,
  etaS: number,
  offsetM: number,
): RemainingEstimate {
  const remainingM = Math.max(0, distanceM - offsetM);
  const remainingS = distanceM > 0 ? (etaS * remainingM) / distanceM : 0;
  return { remainingM, remainingS };
}

/**
 * The next upcoming maneuver at or after the current route offset, or
 * `null` if the traveler is past the last maneuver. Does not mutate the
 * input array.
 */
export function nextManeuver(
  maneuvers: Maneuver[],
  offsetM: number,
): Maneuver | null {
  let best: Maneuver | null = null;
  for (const m of maneuvers) {
    if (m.offset_m >= offsetM) {
      if (best === null || m.offset_m < best.offset_m) {
        best = m;
      }
    }
  }
  return best;
}

export interface ProjectedUnsafePoint {
  point: UnsafePointLike;
  offsetM: number;
}

/**
 * Project every unsafe point onto the route once, recording each one's
 * distance-along-route. Intended to be computed once per route (e.g.
 * memoized by the caller) rather than on every GPS tick — see
 * `alertsAhead`, which just filters this precomputed list.
 */
export function projectUnsafePoints(
  unsafePoints: UnsafePointLike[],
  routeCoordinates: [number, number][],
): ProjectedUnsafePoint[] {
  return unsafePoints.map((p) => {
    const { offsetM } = projectOnRoute(routeCoordinates, p);
    return { point: p, offsetM };
  });
}

/**
 * Filter precomputed, route-projected unsafe points down to those ahead of
 * the current position within `lookaheadM` meters. Cheap enough to call on
 * every GPS update since it does no reprojection.
 */
export function alertsAhead(
  projected: ProjectedUnsafePoint[],
  offsetM: number,
  lookaheadM: number,
): ProjectedUnsafePoint[] {
  const end = offsetM + lookaheadM;
  return projected.filter((p) => p.offsetM >= offsetM && p.offsetM <= end);
}
