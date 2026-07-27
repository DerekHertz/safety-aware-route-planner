// API contract types — kept in sync BY HAND with api/schemas.py (the
// server is the source of truth; a future mobile client reuses this shape).

export interface LatLon {
  lat: number;
  lon: number;
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

export interface RouteAlternative {
  kind: RouteKind;
  geometry: LineString;
  distance_m: number;
  eta_s: number;
  unsafe: UnsafeCounts;
  segments: Segment[];
  unsafe_points: UnsafePoint[];
  /** Extra time versus the fastest route in the same response, as a fraction. */
  detour_pct: number;
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
