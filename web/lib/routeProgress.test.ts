import { describe, expect, it } from "vitest";
import {
  Maneuver,
  alertsAhead,
  endpointDistances,
  nextManeuver,
  projectOnRoute,
  projectUnsafePoints,
  remaining,
} from "./routeProgress";

// A short straight line running north along a meridian near the equator,
// where 1 degree of latitude is ~111,320m — easy to reason about, and turf's
// ellipsoidal calc vs. our loose tolerances agree closely enough here.
const LON = -122.42;
const LAT_START = 37.77;
const STEP_DEG = 0.001; // ~111.3m per step
const POINT_COUNT = 10;
const ROUTE: [number, number][] = Array.from(
  { length: POINT_COUNT },
  (_, i) => [LON, LAT_START + i * STEP_DEG],
);
const METERS_PER_STEP = 111.3 * (STEP_DEG / 0.001);

describe("projectOnRoute", () => {
  it("returns ~0 offRouteM for a point exactly on the line", () => {
    const midIndex = 4;
    const pos = { lon: ROUTE[midIndex][0], lat: ROUTE[midIndex][1] };
    const result = projectOnRoute(ROUTE, pos);
    expect(result.offRouteM).toBeCloseTo(0, 0);
    expect(result.offsetM).toBeGreaterThan(0);
    expect(result.offsetM).toBeCloseTo(midIndex * METERS_PER_STEP, -1);
  });

  it("returns ~50m offRouteM for a point 50m off the line", () => {
    // ~50m east of the route, at roughly the midpoint latitude.
    const midLat = LAT_START + 4 * STEP_DEG;
    const metersPerDegLon = 111_320 * Math.cos((midLat * Math.PI) / 180);
    const offsetLon = 50 / metersPerDegLon;
    const pos = { lon: LON + offsetLon, lat: midLat };
    const result = projectOnRoute(ROUTE, pos);
    expect(result.offRouteM).toBeCloseTo(50, -1);
  });
});

describe("endpointDistances", () => {
  it("is ~0 to the start vertex when the position sits on it", () => {
    const pos = { lon: ROUTE[0][0], lat: ROUTE[0][1] };
    const { startM, endM } = endpointDistances(ROUTE, pos);
    expect(startM).toBeCloseTo(0, 0);
    expect(endM).toBeCloseTo((POINT_COUNT - 1) * METERS_PER_STEP, -1);
  });

  it("is ~0 to the end vertex when the position sits on it", () => {
    const last = ROUTE[ROUTE.length - 1];
    const { startM, endM } = endpointDistances(ROUTE, {
      lon: last[0],
      lat: last[1],
    });
    expect(endM).toBeCloseTo(0, 0);
    expect(startM).toBeCloseTo((POINT_COUNT - 1) * METERS_PER_STEP, -1);
  });

  it("reports BOTH endpoints as far when the position is off-route near the middle — the regression the off-route grace needs", () => {
    // 50m east of the midpoint: a nearest-point projection would clamp the
    // along-route offset toward an endpoint, but the straight-line distance to
    // each actual endpoint stays hundreds of meters, so neither reads as "near".
    const midLat = LAT_START + 4 * STEP_DEG;
    const metersPerDegLon = 111_320 * Math.cos((midLat * Math.PI) / 180);
    const pos = { lon: LON + 50 / metersPerDegLon, lat: midLat };
    const { startM, endM } = endpointDistances(ROUTE, pos);
    expect(startM).toBeGreaterThan(30);
    expect(endM).toBeGreaterThan(30);
  });

  it("returns Infinity for an empty route", () => {
    expect(endpointDistances([], { lon: 0, lat: 0 })).toEqual({
      startM: Infinity,
      endM: Infinity,
    });
  });
});

describe("remaining", () => {
  it("computes proportional remaining distance and time", () => {
    const result = remaining(1000, 200, 400);
    expect(result.remainingM).toBeCloseTo(600, 5);
    expect(result.remainingS).toBeCloseTo(120, 5);
  });

  it("clamps remainingM at 0 when offset exceeds distance", () => {
    const result = remaining(1000, 200, 1500);
    expect(result.remainingM).toBe(0);
    expect(result.remainingS).toBe(0);
  });

  it("guards against divide-by-zero when distanceM is 0", () => {
    const result = remaining(0, 200, 0);
    expect(result.remainingM).toBe(0);
    expect(result.remainingS).toBe(0);
  });
});

describe("nextManeuver", () => {
  const maneuvers: Maneuver[] = [
    { type: "left", angle_deg: -90, offset_m: 100, lon: 0, lat: 0 },
    { type: "right", angle_deg: 90, offset_m: 300, lon: 0, lat: 0 },
    { type: "uturn", angle_deg: 180, offset_m: 500, lon: 0, lat: 0 },
  ];

  it("returns the first maneuver at or after the current offset", () => {
    expect(nextManeuver(maneuvers, 0)?.offset_m).toBe(100);
    expect(nextManeuver(maneuvers, 150)?.offset_m).toBe(300);
    expect(nextManeuver(maneuvers, 300)?.offset_m).toBe(300);
  });

  it("returns null when past the last maneuver", () => {
    expect(nextManeuver(maneuvers, 501)).toBeNull();
  });

  it("does not mutate the input array", () => {
    const copy = [...maneuvers];
    nextManeuver(maneuvers, 0);
    expect(maneuvers).toEqual(copy);
  });
});

describe("projectUnsafePoints + alertsAhead", () => {
  it("includes points within the lookahead window, excludes those outside or already passed", () => {
    const within = {
      lon: ROUTE[5][0],
      lat: ROUTE[5][1],
      type: "unprotected_left",
    };
    const tooFar = {
      lon: ROUTE[9][0],
      lat: ROUTE[9][1],
      type: "unprotected_left",
    };
    const alreadyPassed = {
      lon: ROUTE[1][0],
      lat: ROUTE[1][1],
      type: "uncontrolled_crossing",
    };

    const projected = projectUnsafePoints(
      [within, tooFar, alreadyPassed],
      ROUTE,
    );

    const currentOffsetM = 3 * METERS_PER_STEP; // at index 3
    const lookaheadM = 3 * METERS_PER_STEP; // covers up to ~index 6

    const ahead = alertsAhead(projected, currentOffsetM, lookaheadM);
    const types = ahead.map((a) => a.point);

    expect(types).toContainEqual(within);
    expect(types).not.toContainEqual(tooFar);
    expect(types).not.toContainEqual(alreadyPassed);
  });
});
