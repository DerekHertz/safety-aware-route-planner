// Served-pack coverage on the client (ADR-0014 decision 6).
//
// These mirror api/registry.py, which stays the authority: /route re-checks
// and answers 422 whatever the client decides. Same conventions, which differ
// on purpose and are easy to swap:
//
//   * a point is `{lat, lon}` — the order /route takes on the wire;
//   * a bbox is the manifest's `[west, south, east, north]`;
//   * MapLibre wants `[lng, lat]` pairs — convert with `bboxToLngLatBounds`,
//     never by hand.
//
// Containment is closed on every side, and a null bbox contains every point
// (legal server-side only for a sole served pack: the toy/test deployment).
import { LatLon, PackMeta, ServedPack } from "./types";

/** Closed-interval containment of `p` in `[west, south, east, north]`.
 *  A null bbox — or a malformed one, which the server refuses at startup and
 *  so never sends — contains everything. */
export function bboxContains(bbox: number[] | null, p: LatLon): boolean {
  if (!bbox || bbox.length !== 4) return true;
  const [west, south, east, north] = bbox;
  return p.lat >= south && p.lat <= north && p.lon >= west && p.lon <= east;
}

/** The served pack containing `p`, or null. Served bboxes are disjoint, so at
 *  most one matches. */
export function packForPoint(
  packs: readonly ServedPack[],
  p: LatLon,
): ServedPack | null {
  return packs.find((pack) => bboxContains(pack.bbox, p)) ?? null;
}

/** Whether `p` is routable at all: inside ANY served pack. */
export function insideCoverage(
  packs: readonly ServedPack[],
  p: LatLon,
): boolean {
  return packForPoint(packs, p) !== null;
}

/** What the map frames first: the pack containing the first GPS fix, else the
 *  default (first) pack. Null when there is no bbox to frame. */
export function initialViewBbox(
  packs: readonly ServedPack[],
  firstFix: LatLon | null,
): number[] | null {
  const pack = (firstFix && packForPoint(packs, firstFix)) || packs[0];
  return pack?.bbox ?? null;
}

/** `[west, south, east, north]` as MapLibre's `[[lng, lat], [lng, lat]]`
 *  (south-west corner, north-east corner). */
export function bboxToLngLatBounds(
  bbox: number[],
): [[number, number], [number, number]] {
  const [west, south, east, north] = bbox;
  return [
    [west, south],
    [east, north],
  ];
}

export function regionLabel(region: string): string {
  return region.replace(/_/g, " ");
}

/** Every served region, human-readably, in served order. */
export function coverageLabel(packs: readonly ServedPack[]): string {
  return packs.map((p) => regionLabel(p.region)).join(", ");
}

/** Why /route would refuse this origin/destination pair, as a message for the
 *  user — or null when it is worth asking. Mirrors the server's checks in its
 *  order (origin first), so the client never sends a request that can only
 *  come back 422 "outside every served region" or "different regions". */
export function preflight(
  packs: readonly ServedPack[],
  origin: LatLon,
  destination: LatLon,
): string | null {
  if (packs.length === 0) return null; // no coverage info: let the server say
  const o = packForPoint(packs, origin);
  if (!o) {
    return `The starting point is outside the mapped area (${coverageLabel(packs)}).`;
  }
  const d = packForPoint(packs, destination);
  if (!d) {
    return `The destination is outside the mapped area (${coverageLabel(packs)}).`;
  }
  if (o.region !== d.region) {
    return (
      `The starting point is in ${regionLabel(o.region)} and the destination ` +
      `in ${regionLabel(d.region)}; routing between different regions isn't ` +
      `supported.`
    );
  }
  return null;
}

/** A /meta body as some server sent it: one that predates ADR-0014 step 6
 *  has no `packs`. */
export type RawPackMeta = Omit<PackMeta, "packs"> & { packs?: ServedPack[] };

/** Fill in `packs` for an old server: its one pack is the top-level fields. */
export function normalizeMeta(raw: RawPackMeta): PackMeta {
  if (raw.packs) return raw as PackMeta;
  const { region, bbox, num_edges } = raw;
  return { region, bbox, num_edges, packs: [{ region, bbox, num_edges }] };
}
