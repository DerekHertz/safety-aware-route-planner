// API contract types — mirroring api/schemas.py, which is the source of truth
// (a future mobile client reuses this shape).
//
// Field names and optionality are enforced against the server's OpenAPI schema
// by .github/workflows/schema-sync.yml. TYPES are deliberately not compared:
// the server declares `geometry: dict` and `kind: str` where TypeScript narrows
// to LineString and RouteKind, and that divergence is intentional.

export interface LatLon {
  lat: number;
  lon: number;
}

/** Request body for POST /route. Optional fields fall back to server defaults:
 *  `departure_time` to "now", `detour_budget_pct` to the config value. */
export interface RouteRequest {
  origin: LatLon;
  destination: LatLon;
  departure_time?: string | null;
  safety_enabled?: boolean;
  detour_budget_pct?: number | null;
}

export type RouteKind = "fast" | "balanced" | "safe";
export type Tier = "safe" | "caution" | "unsafe";
export type UnsafeType = "unprotected_left" | "uncontrolled_crossing";

export interface LineString {
  type: "LineString";
  coordinates: [number, number][];
}

export interface UnsafeCounts {
  unprotected_left: number;
  uncontrolled_crossing: number;
  total: number;
}

export interface Segment {
  geometry: LineString;
  tier: Tier;
}

export interface UnsafePoint {
  lon: number;
  lat: number;
  type: UnsafeType;
}

export type ManeuverType = "left" | "right" | "uturn";

export interface Maneuver {
  type: ManeuverType;
  angle_deg: number;
  offset_m: number;
  lon: number;
  lat: number;
}

/** The reproducible description of what a route was optimized for (ADR-0004):
 *  the safety-level label plus the resolved reproducer params. A nav consumer
 *  replays these to reroute at the SAME safety level (ADR-0002). `lambda` is the
 *  weight the level maps to — an internal knob; the UI shows `level`. */
export interface Preference {
  level: RouteKind;
  lambda: number;
  detour_budget_pct: number;
  departure_time: string;
}

export interface RouteAlternative {
  kind: RouteKind;
  geometry: LineString;
  distance_m: number;
  eta_s: number;
  unsafe: UnsafeCounts;
  segments: Segment[];
  unsafe_points: UnsafePoint[];
  maneuvers: Maneuver[];
  /** Extra time versus the fastest route in the same response, as a fraction. */
  detour_pct: number;
  preference: Preference;
  /** Route-artifact contract version; bumped only on a breaking shape change. */
  schema_version: number;
}

/** How far out of the way the router may go for a safer crossing, as a
 *  fraction of the fastest route's time. 0 means "no detour at all". */
export const DETOUR_BUDGET_OPTIONS: { label: string; value: number }[] = [
  { label: "Off", value: 0 },
  { label: "A block", value: 0.1 },
  { label: "A few blocks", value: 0.25 },
  { label: "Whatever it takes", value: 0.5 },
];

export const DEFAULT_DETOUR_BUDGET = 0.25;

export interface RouteResponse {
  routes: RouteAlternative[];
}

/** Request body for POST /reroute (ADR-0008): replan from the current position
 *  to the original destination, carrying a prior artifact's `preference` so the
 *  replacement stays at the SAME safety level. The server recomputes only that
 *  one level — never the full fast/balanced/safe set. */
export interface RerouteRequest {
  origin: LatLon;
  destination: LatLon;
  preference: Preference;
}

/** A reroute yields ONE artifact at the carried level, not a `routes` list. */
export interface RerouteResponse {
  route: RouteAlternative;
}

export interface GeocodeResult {
  name: string;
  lat: number;
  lon: number;
}

export interface PackMeta {
  region: string;
  /** [west, south, east, north]; null when the pack declares no coverage. */
  bbox: number[] | null;
  num_edges: number;
}
