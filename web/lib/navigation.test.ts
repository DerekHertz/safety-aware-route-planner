import { describe, expect, it } from "vitest";
import { ARRIVAL_RADIUS_M, decideNavAction } from "./navigation";
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

describe("decideNavAction", () => {
  it("returns 'none' when there is no progress yet", () => {
    expect(decideNavAction("navigating", null, false)).toBe("none");
  });

  it("returns 'reroute' when navigating and off-route", () => {
    expect(
      decideNavAction("navigating", progress({ offRoute: true }), false),
    ).toBe("reroute");
  });

  it("returns 'none' off-route while a reroute is already in flight", () => {
    expect(
      decideNavAction("navigating", progress({ offRoute: true }), true),
    ).toBe("none");
  });

  it("returns 'arrive' within the arrival radius", () => {
    expect(
      decideNavAction(
        "navigating",
        progress({ remainingM: ARRIVAL_RADIUS_M - 1 }),
        false,
      ),
    ).toBe("arrive");
  });

  it("prefers arrival over reroute when both would apply", () => {
    // Off-route AND within the arrival radius: don't replan onto the doorstep.
    expect(
      decideNavAction(
        "navigating",
        progress({ offRoute: true, remainingM: ARRIVAL_RADIUS_M - 1 }),
        false,
      ),
    ).toBe("arrive");
  });

  it("takes no action once already arrived, even if off-route", () => {
    expect(
      decideNavAction(
        "arrived",
        progress({ offRoute: true, remainingM: ARRIVAL_RADIUS_M - 1 }),
        false,
      ),
    ).toBe("none");
  });

  it("keeps following (no action) when on-route with distance remaining", () => {
    expect(decideNavAction("navigating", progress(), false)).toBe("none");
  });
});
