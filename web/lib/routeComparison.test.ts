import { describe, expect, it } from "vitest";
import {
  compareRoutes,
  formatTimeDelta,
  ROUTE_KIND_ORDER,
} from "./routeComparison";
import { RouteAlternative, RouteKind } from "./types";

// Minimal-but-valid fixture: only eta_s/kind vary across tests, everything
// else is filler that satisfies the RouteAlternative shape.
function route(kind: RouteKind, etaS: number): RouteAlternative {
  return {
    kind,
    geometry: { type: "LineString", coordinates: [] },
    distance_m: 1000,
    eta_s: etaS,
    unsafe: { unprotected_left: 0, uncontrolled_crossing: 0, total: 0 },
    segments: [],
    unsafe_points: [],
    maneuvers: [],
    detour_pct: 0,
    preference: {
      level: kind,
      lambda: 0,
      detour_budget_pct: 0.25,
      departure_time: "2026-09-18T08:00:00",
      traffic_basis: {
        source: "synthetic",
        as_of: "2026-09-18T08:00:00",
        profile_version: "0123456789ab",
      },
    },
    schema_version: 2,
  };
}

describe("compareRoutes", () => {
  it("returns an empty list for an empty response", () => {
    expect(compareRoutes([])).toEqual([]);
  });

  it("orders rows fast/balanced/safe regardless of input order", () => {
    const rows = compareRoutes([
      route("safe", 900),
      route("fast", 600),
      route("balanced", 700),
    ]);
    expect(rows.map((r) => r.route.kind)).toEqual(ROUTE_KIND_ORDER);
  });

  it("computes deltaS against the fastest route in the SAME response", () => {
    const rows = compareRoutes([
      route("fast", 600),
      route("balanced", 700),
      route("safe", 900),
    ]);
    const byKind = Object.fromEntries(rows.map((r) => [r.route.kind, r]));
    expect(byKind.fast.deltaS).toBe(0);
    expect(byKind.balanced.deltaS).toBe(100);
    expect(byKind.safe.deltaS).toBe(300);
  });

  it("flags only the quickest alternative as isFastest", () => {
    const rows = compareRoutes([route("fast", 600), route("safe", 900)]);
    const byKind = Object.fromEntries(rows.map((r) => [r.route.kind, r]));
    expect(byKind.fast.isFastest).toBe(true);
    expect(byKind.safe.isFastest).toBe(false);
  });

  it("treats an exact tie as fastest for both routes", () => {
    const rows = compareRoutes([route("fast", 600), route("safe", 600)]);
    expect(rows.every((r) => r.isFastest)).toBe(true);
    expect(rows.every((r) => r.deltaS === 0)).toBe(true);
  });

  it("drops kinds absent from the response instead of inventing a row", () => {
    // e.g. safety_enabled=false: the server returns just "fast".
    const rows = compareRoutes([route("fast", 600)]);
    expect(rows).toHaveLength(1);
    expect(rows[0].route.kind).toBe("fast");
  });
});

describe("formatTimeDelta", () => {
  it("labels the fastest route instead of +0 min", () => {
    expect(formatTimeDelta(0)).toBe("fastest");
  });

  it("labels a negative delta as fastest too (defensive against float drift)", () => {
    expect(formatTimeDelta(-0.5)).toBe("fastest");
  });

  it("rounds to the nearest minute", () => {
    expect(formatTimeDelta(90)).toBe("+2 min");
    expect(formatTimeDelta(89)).toBe("+1 min");
  });

  it("does not round a sub-minute delta down to fastest", () => {
    expect(formatTimeDelta(20)).toBe("+<1 min");
  });
});
