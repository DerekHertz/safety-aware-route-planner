// "Open in Google Maps" deep link (ADR-0015, 2026-09-24 amendment).
//
// Google's own route for the SAME origin and destination, in Google's own
// app, as a familiar baseline next to our alternatives' unsafe counts. It is a
// plain Maps URL, not a Maps Platform API call: no key, no Google script, no
// prefetch. Nothing reaches Google until the user follows the link.
//
// Deliberately NOT here: waypoints. Handing our `safe` route to Google via
// pinned waypoints is a separate, undecided idea (ADR-0015 "Fallback").
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
import { LatLon } from "./types";

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
