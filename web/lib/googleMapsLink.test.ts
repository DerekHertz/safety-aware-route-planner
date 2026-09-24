import { describe, expect, it } from "vitest";
import { formatLatLng, googleMapsDirectionsUrl } from "./googleMapsLink";

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
