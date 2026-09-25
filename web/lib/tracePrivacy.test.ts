import { describe, expect, it } from "vitest";
import { REROUTE, ROUTE, truncate } from "./__fixtures__/routes";
import { TRIM_M, clipArtifact, distanceM } from "./tracePrivacy";
import { RouteAlternative } from "./types";

// ADR-0018's client obligation, tested on a real artifact's shape (see
// __fixtures__/routes.ts), not a toy.

type Coord = [number, number];
const pt = ([lon, lat]: Coord) => ({ lon, lat });
const first = (r: RouteAlternative) => pt(r.geometry.coordinates[0]);
const last = (r: RouteAlternative) => pt(r.geometry.coordinates.at(-1)!);

/** Every coordinate the clipped artifact would put on the wire. */
function allPoints(r: RouteAlternative): { lon: number; lat: number }[] {
  return [
    ...r.geometry.coordinates.map(pt),
    ...r.segments.flatMap((s) => s.geometry.coordinates.map(pt)),
    ...r.maneuvers.map((m) => ({ lon: m.lon, lat: m.lat })),
    ...r.unsafe_points.map((u) => ({ lon: u.lon, lat: u.lat })),
  ];
}

function minDistance(r: RouteAlternative, to: { lon: number; lat: number }) {
  return Math.min(...allPoints(r).map((p) => distanceM(p, to)));
}

function lengthM(coords: Coord[]): number {
  let s = 0;
  for (let i = 1; i < coords.length; i++) {
    s += distanceM(pt(coords[i - 1]), pt(coords[i]));
  }
  return s;
}

describe("clipArtifact on a real route artifact", () => {
  const clipped = clipArtifact(ROUTE, [])!;

  it("puts nothing within 300 m of either end on the wire", () => {
    expect(clipped).not.toBeNull();
    expect(minDistance(clipped, first(ROUTE))).toBeGreaterThanOrEqual(TRIM_M);
    expect(minDistance(clipped, last(ROUTE))).toBeGreaterThanOrEqual(TRIM_M);
    // ...and cuts no more than it has to: the kept line starts within a few
    // metres of the 300 m circle.
    const g = clipped.geometry.coordinates;
    expect(distanceM(pt(g[0]), first(ROUTE))).toBeLessThan(TRIM_M + 3);
    expect(distanceM(pt(g.at(-1)!), last(ROUTE))).toBeLessThan(TRIM_M + 3);
  });

  it("keeps one contiguous piece of the original line", () => {
    const orig = ROUTE.geometry.coordinates.map((c) => c.join());
    const inner = clipped.geometry.coordinates
      .slice(1, -1)
      .map((c) => c.join());
    const at = orig.indexOf(inner[0]);
    expect(at).toBeGreaterThan(0);
    expect(orig.slice(at, at + inner.length)).toEqual(inner);
    expect(clipped.distance_m).toBeCloseTo(
      lengthM(clipped.geometry.coordinates),
      6,
    );
    expect(clipped.distance_m).toBeLessThan(ROUTE.distance_m - 2 * TRIM_M);
  });

  it("cuts the per-edge segments to the same span, still tiling it", () => {
    const segs = clipped.segments.map((s) => s.geometry.coordinates);
    expect(segs[0][0]).toEqual(clipped.geometry.coordinates[0]);
    expect(segs.at(-1)!.at(-1)).toEqual(clipped.geometry.coordinates.at(-1));
    for (let i = 1; i < segs.length; i++) {
      expect(segs[i][0]).toEqual(segs[i - 1].at(-1));
    }
    expect(segs.reduce((s, c) => s + lengthM(c as Coord[]), 0)).toBeCloseTo(
      clipped.distance_m,
      6,
    );
    for (const s of clipped.segments)
      expect(["safe", "caution", "unsafe"]).toContain(s.tier);
  });

  it("drops the maneuvers in the cut spans and rebases the rest", () => {
    expect(clipped.maneuvers.length).toBeGreaterThan(0);
    expect(clipped.maneuvers.length).toBeLessThan(ROUTE.maneuvers.length);
    // The offset leaks how much was cut unless it is rebased: each kept
    // maneuver's offset is now the distance along the CLIPPED line.
    const g = clipped.geometry.coordinates;
    for (const m of clipped.maneuvers) {
      const i = g.findIndex(([lon, lat]) => lon === m.lon && lat === m.lat);
      expect(i).toBeGreaterThan(0);
      expect(m.offset_m).toBeCloseTo(lengthM(g.slice(0, i + 1)), 3);
    }
  });

  it("changes nothing that is not a place", () => {
    const {
      geometry,
      segments,
      maneuvers,
      unsafe_points,
      distance_m,
      ...rest
    } = clipped;
    const {
      geometry: g0,
      segments: s0,
      maneuvers: m0,
      unsafe_points: u0,
      distance_m: d0,
      ...rest0
    } = ROUTE;
    void [geometry, segments, maneuvers, unsafe_points, distance_m];
    void [g0, s0, m0, u0, d0];
    expect(rest).toEqual(rest0);
    // Both unsafe points sit mid-route, >1 km from either end: kept.
    expect(clipped.unsafe_points).toEqual(ROUTE.unsafe_points);
  });

  it("does not mutate its input", () => {
    const before = structuredClone(ROUTE);
    clipArtifact(ROUTE, [{ ...first(ROUTE), accuracy_m: 20 }]);
    expect(ROUTE).toEqual(before);
  });
});

describe("clipArtifact against the trip's other private points", () => {
  it("widens a cut by an anchor's accuracy", () => {
    const c = clipArtifact(ROUTE, [{ ...first(ROUTE), accuracy_m: 60 }])!;
    expect(minDistance(c, first(ROUTE))).toBeGreaterThanOrEqual(TRIM_M + 60);
  });

  it("keeps the longest clear stretch when an anchor sits mid-route", () => {
    // Say the planned origin was here (navigation started late): nothing
    // near it may be uploaded, including the unsafe point next to it.
    const u = ROUTE.unsafe_points[0];
    const c = clipArtifact(ROUTE, [{ lon: u.lon, lat: u.lat }])!;
    expect(minDistance(c, u)).toBeGreaterThanOrEqual(TRIM_M);
    expect(c.unsafe_points).toEqual([]);
    expect(minDistance(c, first(ROUTE))).toBeGreaterThanOrEqual(TRIM_M);
    expect(minDistance(c, last(ROUTE))).toBeGreaterThanOrEqual(TRIM_M);
    expect(c.distance_m).toBeGreaterThan(900);
  });

  it("clips a reroute at its own start too", () => {
    const c = clipArtifact(REROUTE, [])!;
    expect(minDistance(c, first(REROUTE))).toBeGreaterThanOrEqual(TRIM_M);
    expect(minDistance(c, last(REROUTE))).toBeGreaterThanOrEqual(TRIM_M);
  });

  it("returns null when nothing survives", () => {
    const short = truncate(ROUTE, 550);
    expect(clipArtifact(short, [])).toBeNull();
    const mid = pt(ROUTE.geometry.coordinates[110]);
    expect(clipArtifact(ROUTE, [{ ...mid, accuracy_m: 5000 }])).toBeNull();
  });
});
