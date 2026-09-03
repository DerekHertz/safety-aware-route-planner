import { describe, expect, it } from "vitest";
import { ARRIVAL_RADIUS_M, hasArrived, shouldReroute } from "./navigation";
import { RouteProgressState } from "./useRouteProgress";

// A toy progress reading: on-route, plenty of distance left. Individual tests
// override just the fields they exercise.
function progress(over: Partial<RouteProgressState> = {}): RouteProgressState {
  return {
    offsetM: 100,
    offRouteM: 0,
    offRoute: false,
    remainingM: 500,
    remainingS: 120,
    nextManeuver: null,
    alerts: [],
    ...over,
  };
}

describe("hasArrived", () => {
  it("is true within the arrival radius of the destination", () => {
    expect(hasArrived(ARRIVAL_RADIUS_M - 1)).toBe(true);
  });

  it("is false at or beyond the arrival radius", () => {
    expect(hasArrived(ARRIVAL_RADIUS_M)).toBe(false);
    expect(hasArrived(200)).toBe(false);
  });
});

describe("shouldReroute", () => {
  it("reroutes when navigating and off-route with none in flight", () => {
    expect(
      shouldReroute("navigating", progress({ offRoute: true }), false),
    ).toBe(true);
  });

  it("does not reroute while a reroute is already in flight", () => {
    expect(
      shouldReroute("navigating", progress({ offRoute: true }), true),
    ).toBe(false);
  });

  it("does not reroute once arrived, even if off-route", () => {
    expect(shouldReroute("arrived", progress({ offRoute: true }), false)).toBe(
      false,
    );
  });

  it("does not reroute while on-route", () => {
    expect(shouldReroute("navigating", progress(), false)).toBe(false);
  });

  it("does not reroute when there is no progress yet", () => {
    expect(shouldReroute("navigating", null, false)).toBe(false);
  });
});
