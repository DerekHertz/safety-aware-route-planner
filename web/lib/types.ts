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
}

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
