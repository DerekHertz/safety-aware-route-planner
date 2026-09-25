// API contract types — mirroring api/schemas.py, which is the source of truth
// (a future mobile client reuses this shape). The trip-trace ingest types at
// the end mirror commute/schemas.py instead: a separate service (ADR-0018).
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
  /** Expected control delay at this maneuver, seconds (ADR-0016): waiting
   *  time, not a safety score. */
  expected_wait_s: number;
}

export type ManeuverType = "left" | "right" | "uturn";

export interface Maneuver {
  type: ManeuverType;
  angle_deg: number;
  offset_m: number;
  lon: number;
  lat: number;
}

/** What traffic inputs a route was computed against (ADR-0004 schema v2).
 *
 *  `source` is "synthetic" while the deterministic `[sim]` profiles are the
 *  volume model (ADR-0010); a real feed changes this VALUE, not this shape.
 *
 *  `as_of` is when those inputs were observed. Under the synthetic model that
 *  is a pure function of the departure clock, so today it EQUALS
 *  `Preference.departure_time` — a deliberate, documented duplication that
 *  ends when a real feed lands and the observation time stops being the
 *  departure time. Don't build on the equality.
 *
 *  `profile_version` is a content hash of the generating profiles, so
 *  "the synthetic profiles were retuned" is detectable from two artifacts
 *  alone — the one thing that can actually differ under a deterministic
 *  model. */
export interface TrafficBasis {
  source: string;
  as_of: string;
  profile_version: string;
}

/** The reproducible description of what a route was optimized for (ADR-0004):
 *  the safety-level label plus the resolved reproducer params. A nav consumer
 *  replays these to reroute at the SAME safety level (ADR-0002). `lambda` is the
 *  weight the level maps to — an internal knob; the UI shows `level`.
 *
 *  This is the shape the server EMITS: `traffic_basis` is always present, so a
 *  consumer reading an artifact never null-checks it. */
export interface Preference {
  level: RouteKind;
  lambda: number;
  detour_budget_pct: number;
  departure_time: string;
  traffic_basis: TrafficBasis;
}

/** A preference sent back to the server on /reroute. Identical to `Preference`
 *  except that `traffic_basis` is optional, because a client mid-drive may be
 *  following a v1 artifact that has none — a required request field would not
 *  be an additive contract change and would 422 that in-flight nav session.
 *  `Preference` is assignable to this, so callers just pass the artifact's own.
 *  The server ignores it and reports the basis of the snapshot it actually
 *  computes. */
export interface CarriedPreference {
  level: RouteKind;
  lambda: number;
  detour_budget_pct: number;
  departure_time: string;
  traffic_basis?: TrafficBasis;
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
  /** Expected intersection waits over the whole route, seconds (ADR-0016).
   *  Already included in `eta_s`. */
  control_delay_s: number;
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
  preference: CarriedPreference;
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

/** One served pack's coverage (ADR-0014 decision 6). */
export interface ServedPack {
  region: string;
  /** [west, south, east, north]; null when the pack declares no coverage
   *  (only ever the sole served pack: a toy/test deployment). */
  bbox: number[] | null;
  num_edges: number;
}

/** GET /meta. The top-level fields describe the DEFAULT pack (the first
 *  served); `packs` lists every served pack, default first. A server that
 *  predates `packs` omits it — `fetchMeta` fills it in, so everything past
 *  that boundary can rely on it. */
export interface PackMeta {
  region: string;
  /** [west, south, east, north]; null when the pack declares no coverage. */
  bbox: number[] | null;
  num_edges: number;
  packs: ServedPack[];
}

// ---------------------------------------------------------------------------
// Commute planner: trip-trace ingest (ADR-0017, contract in ADR-0018).
//
// A SEPARATE service from the route service, mirroring commute/schemas.py and
// held to it by the same schema-sync check. Every `/v1` call carries
// `Authorization: Bearer <tester token>`. Both writes are idempotent: an
// identical resend answers 200, a different body under the same key 409.
// Every error body is `{ detail: string }`, 422 included.
//
// Client obligations the server cannot check (ADR-0018): the 300 m trim at
// both ends of the trip applies to the fixes AND to every uploaded artifact's
// geometry, maneuvers and unsafe points; a sealed chunk's content never
// changes; `fixes[].t` is strictly increasing within a chunk.

/** Milliseconds since the Unix epoch, UTC, as an INTEGER: the unit of
 *  `GeolocationPosition.timestamp`, rounded. Never seconds, never ISO. */
export type EpochMs = number;

/** One GPS fix. `speed_mps` and `heading_deg` are null when the device does
 *  not report them (send null for NaN, too). At most 600 per chunk. */
export interface TraceFix {
  t: EpochMs;
  lat: number;
  lon: number;
  speed_mps: number | null;
  accuracy_m: number;
  heading_deg: number | null;
}

/** A route artifact the client began following at `effective_at`: the one
 *  navigation started with, or a reroute's replacement. Clipped by 300 m at
 *  each end before upload; the server stores it opaquely. */
export interface FollowedArtifact {
  effective_at: EpochMs;
  artifact: RouteAlternative;
}

/** Body of `PUT /v1/trips/{trip_id}/chunks/{seq}`. At least one fix or one
 *  artifact; `artifacts` strictly increasing in `effective_at`. Body limit
 *  1 MiB. */
export interface TraceChunk {
  fixes: TraceFix[];
  artifacts?: FollowedArtifact[];
}

/** 201 when stored, 200 (`created: false`) for an identical replay. */
export interface ChunkReceipt {
  trip_id: string;
  seq: number;
  created: boolean;
}

/** What the artifact being followed when the trip ended predicted: its
 *  `eta_s`, counted from `effective_at`, its `preference.level`, and its
 *  `preference.traffic_basis.profile_version` (null for a v1 artifact). */
export interface EtaPrediction {
  effective_at: EpochMs;
  eta_s: number;
  level: RouteKind;
  profile_version: string | null;
}

/** Body of `POST /v1/trips/{trip_id}/end`: one predicted-versus-actual ETA
 *  log row. Times only, never a place. `arrived` is false when navigation was
 *  stopped before arrival; `prediction` is null only with no artifact. */
export interface TripEnd {
  ended_at: EpochMs;
  arrived: boolean;
  prediction: EtaPrediction | null;
}

/** 201 when logged, 200 (`created: false`) for an identical replay. */
export interface TripEndReceipt {
  trip_id: string;
  created: boolean;
}

/** `GET /v1/me`: the label the owner gave this tester token. For checking a
 *  token at opt-in; 401 means it is unknown or revoked. */
export interface TesterInfo {
  label: string;
}
