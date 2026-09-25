// Test fixtures: real route artifacts, and a helper to cut one short.
import fixture from "./berkeley-route.json";
import { RouteAlternative } from "../types";

type Coord = [number, number];

/** A real artifact from the berkeley_oakland pack: 4 km, 222 vertices, 42
 *  per-edge segments, 22 maneuvers, 2 unsafe points. */
export const ROUTE = fixture.route as unknown as RouteAlternative;
/** A real /reroute answer toward the same destination, starting ~170 m off
 *  ROUTE's midpoint. */
export const REROUTE = fixture.reroute as unknown as RouteAlternative;

function hav([lon1, lat1]: Coord, [lon2, lat2]: Coord): number {
  const r = (d: number) => (d * Math.PI) / 180;
  const s =
    Math.sin(r(lat2 - lat1) / 2) ** 2 +
    Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2;
  return 2 * 6_371_000 * Math.asin(Math.sqrt(s));
}

/** The first `maxM` metres of a real artifact, still artifact-shaped. */
export function truncate(r: RouteAlternative, maxM: number): RouteAlternative {
  const coords: Coord[] = [];
  let s = 0;
  for (const c of r.geometry.coordinates) {
    const step = coords.length ? hav(coords[coords.length - 1], c) : 0;
    if (s + step > maxM) break;
    s += step;
    coords.push(c);
  }
  return {
    ...r,
    geometry: { type: "LineString", coordinates: coords },
    segments: [
      { geometry: { type: "LineString", coordinates: coords }, tier: "safe" },
    ],
    maneuvers: r.maneuvers.filter((m) => m.offset_m <= s),
    unsafe_points: [],
    distance_m: s,
  };
}
