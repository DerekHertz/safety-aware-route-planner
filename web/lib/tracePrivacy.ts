// On-device privacy rules for trip traces (ADR-0017, ADR-0018).
//
// Everything here is pure: no storage, no network, no React. The recorder
// (tripRecorder.ts) applies these rules and nothing else decides what leaves
// the phone, so this file is the one to review for "can an endpoint leak?".
//
// THE RULE, for fixes and artifacts alike: nothing uploaded lies within
// TRIM_M of a private point, measured as STRAIGHT-LINE (great-circle)
// distance between the two points' uncertainty disks:
//
//     separation(a, b) = haversine(a, b) - accuracy(a) - accuracy(b)
//
// and a point is clear of b when separation >= TRIM_M. Straight-line, not
// along-track, on purpose: along-track distance is always >= straight-line,
// so it releases sooner, and it grows while a parked phone's GPS jitters,
// which would release fixes next to a parked car. Subtracting the reported
// accuracies makes every error land on the side of holding more back.
//
// The private points (anchors) of a trip: its first accepted fix, the start
// and end of the artifact navigation began with, the end of every reroute,
// and the navigation destination. Fixes additionally obey the trailing rule:
// a fix is sealed only while it is TRIM_M clear of the latest fix (the head),
// and whatever is not yet clear when the trip ends is discarded. That rule
// is about where the car actually stopped, which only fixes can show; an
// artifact is a PLANNED route, clipped at its own ends, and its passing a
// point where a trip was cut short says nothing about the stop.
//
// Residual risk, accepted: a car that seals a fix, drives more than TRIM_M
// away, then comes back and stops beside it somewhere other than the planned
// destination. Nothing on the phone can know the stop in advance.
//
// No coordinate is ever logged, here or anywhere in the recorder.

import type { LatLon, RouteAlternative, Segment, TraceFix } from "./types";

/** Radius, metres, kept on the phone around each end of a trip (ADR-0017). */
export const TRIM_M = 300;
/** Fixes reporting worse accuracy than this are not recorded at all. */
export const MAX_ACCURACY_M = 100;
/** About 1 Hz: a fix closer in time than this to the last one is skipped. */
export const MIN_FIX_INTERVAL_MS = 900;
/** A fix implying more than this speed from the last one is a GPS jump. */
export const MAX_SPEED_MPS = 70;

/** A private point, with its own uncertainty (0 for a route vertex). */
export interface Anchor extends LatLon {
  accuracy_m?: number;
}

const EARTH_RADIUS_M = 6_371_000; // pyref/geo.py's, so offsets agree
const toRad = (d: number) => (d * Math.PI) / 180;

/** Great-circle metres between two points. */
export function distanceM(a: LatLon, b: LatLon): number {
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(s)));
}

/** Distance between two uncertainty disks: 0 or less when they overlap. */
export function separationM(a: Anchor, b: Anchor): number {
  return distanceM(a, b) - (a.accuracy_m ?? 0) - (b.accuracy_m ?? 0);
}

/** Whether `p` is at least `trimM` clear of every anchor. */
export function clearOf(p: Anchor, anchors: Anchor[], trimM = TRIM_M): boolean {
  return anchors.every((a) => separationM(p, a) >= trimM);
}

// --- fixes ---------------------------------------------------------------

/** A reading as the geolocation stream delivers it: the time may be
 *  fractional, and speed/heading may be NaN or out of range. */
export interface RawFix {
  t: number;
  lat: number;
  lon: number;
  speed_mps: number | null;
  accuracy_m: number;
  heading_deg: number | null;
}

const finite = (x: number | null | undefined): x is number =>
  typeof x === "number" && Number.isFinite(x);

/** The wire form of a reading, or null if it is unusable. `t` becomes an
 *  integer epoch-ms; a speed or heading the server would refuse becomes null
 *  rather than costing the whole fix. */
export function toTraceFix(raw: RawFix): TraceFix | null {
  if (!finite(raw.t) || !finite(raw.lat) || !finite(raw.lon)) return null;
  if (Math.abs(raw.lat) > 90 || Math.abs(raw.lon) > 180) return null;
  if (!finite(raw.accuracy_m) || raw.accuracy_m < 0) return null;
  if (raw.accuracy_m > MAX_ACCURACY_M) return null;
  const speed =
    finite(raw.speed_mps) && raw.speed_mps >= 0 ? raw.speed_mps : null;
  const heading =
    finite(raw.heading_deg) && raw.heading_deg >= 0 && raw.heading_deg <= 360
      ? raw.heading_deg
      : null;
  return {
    t: Math.round(raw.t),
    lat: raw.lat,
    lon: raw.lon,
    speed_mps: speed,
    accuracy_m: raw.accuracy_m,
    heading_deg: heading,
  };
}

/** Whether `fix` may follow `head` in the trace: strictly later (the server
 *  requires increasing `t`), about 1 Hz, and not a jump no car could make.
 *  A rejected fix is dropped entirely: it is not stored and never becomes
 *  the head, so a GPS jump cannot release the held-back tail. */
export function followsHead(head: TraceFix | null, fix: TraceFix): boolean {
  if (!head) return true;
  const dt = fix.t - head.t;
  if (dt < MIN_FIX_INTERVAL_MS) return false;
  return distanceM(head, fix) <= MAX_SPEED_MPS * (dt / 1000);
}

/** How many fixes at the front of `pending` (time order) are clear of the
 *  head, so may be sealed now. A prefix, not a filter: once one fix is still
 *  inside the trailing TRIM_M, every later one waits too, which keeps both
 *  the time order and the rule "only fixes TRIM_M behind the latest one are
 *  flushed" (ADR-0018) even on a road that doubles back. */
export function sealablePrefix(
  pending: TraceFix[],
  head: TraceFix | null,
  trimM = TRIM_M,
): number {
  if (!head) return 0;
  let n = 0;
  while (n < pending.length && separationM(pending[n], head) >= trimM) n++;
  return n;
}

// --- artifacts -----------------------------------------------------------

/** Sampling step along the route when finding where it clears the anchors.
 *  Every original vertex is also sampled, so every uploaded coordinate has
 *  been checked, not interpolated past. */
const SAMPLE_STEP_M = 1;
/** Float slack when comparing along-route distances from different sums. */
const EPS_M = 1e-6;

interface Sample extends LatLon {
  s: number;
  clear: boolean;
}

type Coord = [number, number];
const ll = ([lon, lat]: Coord): LatLon => ({ lon, lat });

function cumulative(coords: Coord[]): number[] {
  const cum = [0];
  for (let i = 1; i < coords.length; i++) {
    cum.push(cum[i - 1] + distanceM(ll(coords[i - 1]), ll(coords[i])));
  }
  return cum;
}

function polylineLength(coords: Coord[]): number {
  const cum = cumulative(coords);
  return cum[cum.length - 1];
}

/**
 * The artifact with every place within `trimM` of a private point removed,
 * or null when nothing is left to upload (ADR-0018's client obligation).
 *
 * The private points are `anchors` plus the artifact's OWN two ends, so a
 * reroute is clipped at its start too. Of the stretches of the line that are
 * clear of all of them, the longest is kept, as ONE LineString:
 *
 * - `geometry` and each per-edge `segments[i]` are cut to that stretch, with
 *   an interpolated point at each cut; segments outside it are dropped.
 * - maneuvers and unsafe points outside it, or not themselves clear, are
 *   dropped.
 * - `maneuvers[].offset_m` is rebased to the clipped line and `distance_m`
 *   becomes its length. Left as they were, the two would say exactly how far
 *   back along the road each endpoint lies.
 * - Everything that is not a place (`eta_s`, `preference`, the unsafe counts,
 *   `control_delay_s`, ...) is unchanged: `eta_s` is the prediction the ETA
 *   log compares against, and it still describes the whole route.
 *
 * Fails closed: if any kept geometry coordinate were not clear, it returns
 * null rather than upload it. The input is not mutated.
 */
export function clipArtifact(
  artifact: RouteAlternative,
  anchors: Anchor[],
  trimM = TRIM_M,
): RouteAlternative | null {
  const coords = artifact.geometry.coordinates as Coord[];
  if (coords.length < 2) return null;
  const centers: Anchor[] = [
    ...anchors,
    ll(coords[0]),
    ll(coords[coords.length - 1]),
  ];
  const isClear = (p: LatLon) => clearOf(p, centers, trimM);

  // Sample the line: every vertex, plus points every SAMPLE_STEP_M between.
  const cum = cumulative(coords);
  const samples: Sample[] = [];
  const push = (lon: number, lat: number, s: number) =>
    samples.push({ lon, lat, s, clear: isClear({ lon, lat }) });
  for (let i = 0; i < coords.length - 1; i++) {
    const [lon1, lat1] = coords[i];
    const [lon2, lat2] = coords[i + 1];
    const len = cum[i + 1] - cum[i];
    const steps = Math.max(1, Math.ceil(len / SAMPLE_STEP_M));
    push(lon1, lat1, cum[i]);
    for (let k = 1; k < steps; k++) {
      const u = k / steps;
      push(
        lon1 + u * (lon2 - lon1),
        lat1 + u * (lat2 - lat1),
        cum[i] + u * len,
      );
    }
  }
  const [lastLon, lastLat] = coords[coords.length - 1];
  push(lastLon, lastLat, cum[cum.length - 1]);

  // The longest run of consecutive clear samples.
  let best: [number, number] | null = null;
  for (let i = 0; i < samples.length; i++) {
    if (!samples[i].clear) continue;
    let j = i;
    while (j + 1 < samples.length && samples[j + 1].clear) j++;
    if (
      j > i &&
      (!best ||
        samples[j].s - samples[i].s > samples[best[1]].s - samples[best[0]].s)
    ) {
      best = [i, j];
    }
    i = j;
  }
  if (!best) return null;
  const start = samples[best[0]];
  const end = samples[best[1]];
  const a = start.s;
  const b = end.s;
  const cutA: Coord = [start.lon, start.lat];
  const cutB: Coord = [end.lon, end.lat];

  // geometry: the cut points and the original vertices strictly between.
  const inner: Coord[] = [];
  let firstInner = -1;
  for (let i = 0; i < coords.length; i++) {
    if (cum[i] > a + EPS_M && cum[i] < b - EPS_M) {
      if (firstInner < 0) firstInner = i;
      inner.push(coords[i]);
    }
  }
  const geometry: Coord[] = [cutA, ...inner, cutB];
  if (!geometry.every((c) => isClear(ll(c)))) return null; // fail closed
  const distance = polylineLength(geometry);
  // Along the ORIGINAL line minus along the CLIPPED line, for any point
  // after the first cut: what a maneuver offset must lose.
  const shift =
    firstInner >= 0
      ? cum[firstInner] - distanceM(ll(cutA), ll(coords[firstInner]))
      : a;

  // segments: per-edge pieces tiling the line; cut each to [a, b].
  const segments: Segment[] = [];
  let segStart = 0;
  for (const seg of artifact.segments) {
    const sc = seg.geometry.coordinates as Coord[];
    const scum = cumulative(sc).map((x) => x + segStart);
    const s0 = scum[0];
    const s1 = scum[scum.length - 1];
    segStart = s1;
    if (sc.length < 2 || s1 <= a + EPS_M || s0 >= b - EPS_M) continue;
    const out: Coord[] = [s0 < a - EPS_M ? cutA : sc[0]];
    for (let j = 1; j < sc.length - 1; j++) {
      if (scum[j] > a + EPS_M && scum[j] < b - EPS_M) out.push(sc[j]);
    }
    out.push(s1 > b + EPS_M ? cutB : sc[sc.length - 1]);
    if (out.length < 2 || !out.every((c) => isClear(ll(c)))) continue;
    segments.push({
      ...seg,
      geometry: { type: "LineString", coordinates: out },
    });
  }

  const maneuvers = artifact.maneuvers
    .filter(
      (m) => m.offset_m >= a - EPS_M && m.offset_m <= b + EPS_M && isClear(m),
    )
    .map((m) => ({ ...m, offset_m: Math.max(0, m.offset_m - shift) }));

  // Unsafe points carry no offset: place each at its nearest sample.
  const unsafePoints = artifact.unsafe_points.filter((u) => {
    if (!isClear(u)) return false;
    let nearest = samples[0];
    let nearestD = Infinity;
    for (const smp of samples) {
      const d = distanceM(smp, u);
      if (d < nearestD) {
        nearestD = d;
        nearest = smp;
      }
    }
    return nearest.s >= a && nearest.s <= b;
  });

  return {
    ...artifact,
    geometry: { type: "LineString", coordinates: geometry },
    segments,
    maneuvers,
    unsafe_points: unsafePoints,
    distance_m: distance,
  };
}
