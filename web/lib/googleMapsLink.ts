// "Open in Google Maps" deep link (ADR-0015, 2026-09-24 amendment).
//
// Google's own route for the SAME origin and destination, in Google's own
// app, as a familiar baseline next to our alternatives' unsafe counts. It is a
// plain Maps URL, not a Maps Platform API call: no key, no Google script, no
// prefetch. Nothing reaches Google until the user follows the link.
//
// `googleMapsNavigateUrl` is the other direction (ADR-0020): it hands one of
// OUR routes to Google Maps for voice turn-by-turn, through the waypoints the
// server pinned (`RouteAlternative.handoff`). The comparison link above stays
// waypoint-free on purpose: it shows Google's own choice.
//
// Format, per https://developers.google.com/maps/documentation/urls/get-started
// ("Directions" action):
//
//   https://www.google.com/maps/dir/?api=1&origin=LAT,LNG&destination=LAT,LNG&travelmode=driving
//
//   * coordinates are "lat,lng" — the same order as our `{lat, lon}`, the
//     opposite of MapLibre's `[lng, lat]`;
//   * values are URL-encoded (the comma becomes %2C);
//   * there is no departure- or arrival-time parameter, so the link cannot
//     carry the planner's departure time: Google plans for "now".
import { LatLon, RouteAlternative } from "./types";

export const GOOGLE_MAPS_DIRECTIONS_BASE = "https://www.google.com/maps/dir/";

/** Decimal places kept: 1e-6 degrees is about 11 cm, well inside GPS noise
 *  and far finer than any snap, so nothing routable changes. */
export const COORD_DECIMALS = 6;

/** `lat,lng` rounded to COORD_DECIMALS, without trailing zeros. */
export function formatLatLng(p: LatLon): string {
  const round = (x: number) => String(Number(x.toFixed(COORD_DECIMALS)));
  return `${round(p.lat)},${round(p.lon)}`;
}

/** A Google Maps driving-directions URL from `origin` to `destination` — the
 *  coordinates the client sent to /route. */
export function googleMapsDirectionsUrl(
  origin: LatLon,
  destination: LatLon,
): string {
  const params = new URLSearchParams({
    api: "1",
    origin: formatLatLng(origin),
    destination: formatLatLng(destination),
    travelmode: "driving",
  });
  return `${GOOGLE_MAPS_DIRECTIONS_BASE}?${params.toString()}`;
}

/** The most waypoints a Directions URL may carry outside mobile browsers —
 *  the Android and iOS Maps apps included (mobile browsers take only 3). The
 *  server caps its pins at the same number (`[handoff] max_waypoints`). */
export const MAX_WAYPOINTS = 9;

/** A Google Maps link that drives `origin` -> `destination` through
 *  `waypoints`, in order, and starts turn-by-turn navigation (ADR-0020).
 *
 *  `dir_action=navigate` starts navigation only when the origin is at or near
 *  the device's location; otherwise Google shows a route preview. */
export function googleMapsNavigateUrl(
  origin: LatLon,
  destination: LatLon,
  waypoints: LatLon[],
): string {
  if (waypoints.length > MAX_WAYPOINTS) {
    throw new Error(
      `Google Maps accepts at most ${MAX_WAYPOINTS} waypoints, got ${waypoints.length}`,
    );
  }
  const params = new URLSearchParams({
    api: "1",
    origin: formatLatLng(origin),
    destination: formatLatLng(destination),
    travelmode: "driving",
  });
  if (waypoints.length > 0) {
    params.set("waypoints", waypoints.map(formatLatLng).join("|"));
  }
  params.set("dir_action", "navigate");
  return `${GOOGLE_MAPS_DIRECTIONS_BASE}?${params.toString()}`;
}

/** What the handoff cannot promise, or null when it can: the flagged
 *  maneuvers Google may still take that this route itself avoids, because
 *  the waypoint cap ran out before the pins could steer past them. */
export function handoffShortfall(route: RouteAlternative): string | null {
  if (!route.handoff) return null;
  const extra = route.handoff.predicted_unsafe - route.unsafe.total;
  if (extra <= 0) return null;
  return (
    `Google Maps may still take ${extra} flagged maneuver` +
    `${extra === 1 ? "" : "s"} this route avoids. Watch the route it shows.`
  );
}
