import { describe, expect, it } from "vitest";
import { RouteAlternative } from "./types";
import {
  formatLatLng,
  googleMapsDirectionsUrl,
  googleMapsNavigateUrl,
  handoffShortfall,
} from "./googleMapsLink";

// Far from (0, 0) and with |lat| != |lon|, so a swapped pair is detectable.
const ORIGIN = { lat: 37.8716, lon: -122.2727 };
const DEST = { lat: 37.8044, lon: -122.2712 };

describe("formatLatLng", () => {
  it("writes lat first, then lng — Google's order and ours, not MapLibre's", () => {
    expect(formatLatLng(ORIGIN)).toBe("37.8716,-122.2727");
  });

  it("rounds to 6 decimals", () => {
    expect(formatLatLng({ lat: 37.123456789, lon: -122.987654321 })).toBe(
      "37.123457,-122.987654",
    );
  });

  it("drops trailing zeros and never writes a negative zero", () => {
    expect(formatLatLng({ lat: 40.5, lon: -74 })).toBe("40.5,-74");
    expect(formatLatLng({ lat: -0.0000001, lon: 0 })).toBe("0,0");
  });
});

describe("googleMapsDirectionsUrl", () => {
  const url = new URL(googleMapsDirectionsUrl(ORIGIN, DEST));

  it("targets the documented Directions action", () => {
    expect(url.origin).toBe("https://www.google.com");
    expect(url.pathname).toBe("/maps/dir/");
    expect(url.searchParams.get("api")).toBe("1");
    expect(url.searchParams.get("travelmode")).toBe("driving");
  });

  it("carries the same origin and destination, as lat,lng", () => {
    expect(url.searchParams.get("origin")).toBe("37.8716,-122.2727");
    expect(url.searchParams.get("destination")).toBe("37.8044,-122.2712");
  });

  it("URL-encodes the comma between lat and lng", () => {
    const raw = googleMapsDirectionsUrl(ORIGIN, DEST);
    expect(raw).toContain("origin=37.8716%2C-122.2727");
    expect(raw).toContain("destination=37.8044%2C-122.2712");
  });

  it("adds no waypoints and nothing the docs don't define", () => {
    expect([...url.searchParams.keys()].sort()).toEqual([
      "api",
      "destination",
      "origin",
      "travelmode",
    ]);
  });

  it("is exactly the documented shape", () => {
    expect(googleMapsDirectionsUrl(ORIGIN, DEST)).toBe(
      "https://www.google.com/maps/dir/?api=1" +
        "&origin=37.8716%2C-122.2727" +
        "&destination=37.8044%2C-122.2712" +
        "&travelmode=driving",
    );
  });
});

describe("googleMapsNavigateUrl", () => {
  const PINS = [
    { lat: 37.85, lon: -122.27 },
    { lat: 37.83, lon: -122.2655555 },
  ];

  it("routes through the waypoints in order and asks for navigation", () => {
    const url = new URL(googleMapsNavigateUrl(ORIGIN, DEST, PINS));
    expect(url.searchParams.get("origin")).toBe("37.8716,-122.2727");
    expect(url.searchParams.get("destination")).toBe("37.8044,-122.2712");
    expect(url.searchParams.get("waypoints")).toBe(
      "37.85,-122.27|37.83,-122.265556",
    );
    expect(url.searchParams.get("dir_action")).toBe("navigate");
    expect(url.searchParams.get("travelmode")).toBe("driving");
  });

  it("URL-encodes the pipe between waypoints", () => {
    expect(googleMapsNavigateUrl(ORIGIN, DEST, PINS)).toContain(
      "waypoints=37.85%2C-122.27%7C37.83%2C-122.265556",
    );
  });

  it("omits waypoints when there are none", () => {
    const url = new URL(googleMapsNavigateUrl(ORIGIN, DEST, []));
    expect(url.searchParams.has("waypoints")).toBe(false);
    expect(url.searchParams.get("dir_action")).toBe("navigate");
  });

  it("refuses more waypoints than the Maps apps accept", () => {
    const ten = Array.from({ length: 10 }, (_, i) => ({
      lat: 37 + i / 100,
      lon: -122,
    }));
    expect(() => googleMapsNavigateUrl(ORIGIN, DEST, ten)).toThrow(/9/);
  });
});

describe("handoffShortfall", () => {
  const route = (total: number, predicted: number | null) =>
    ({
      unsafe: { unprotected_left: total, uncontrolled_crossing: 0, total },
      handoff:
        predicted === null
          ? null
          : { waypoints: [], predicted_unsafe: predicted },
    }) as unknown as RouteAlternative;

  it("is silent when the pins fully steer the route", () => {
    expect(handoffShortfall(route(0, 0))).toBeNull();
    expect(handoffShortfall(route(2, 2))).toBeNull();
  });

  it("is silent on a reroute artifact, which carries no handoff", () => {
    expect(handoffShortfall(route(0, null))).toBeNull();
  });

  it("counts only what Google may add beyond the route's own", () => {
    expect(handoffShortfall(route(1, 2))).toMatch(
      /may still take 1 flagged maneuver /,
    );
    expect(handoffShortfall(route(0, 3))).toMatch(/3 flagged maneuvers/);
  });
});
