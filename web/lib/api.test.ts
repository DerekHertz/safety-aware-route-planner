import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchReroute } from "./api";
import { LatLon, Preference, RouteAlternative } from "./types";

const ORIGIN: LatLon = { lat: 37.87, lon: -122.27 };
const DEST: LatLon = { lat: 37.85, lon: -122.25 };
// A carried preference, as ADR-0008 requires: the replan stays at THIS level.
const PREF: Preference = {
  level: "safe",
  lambda: 1.5,
  detour_budget_pct: 0.25,
  departure_time: "2026-09-02T14:56:00",
};

// A minimal artifact stand-in — the client returns it verbatim, it doesn't
// inspect the shape, so only enough fields to satisfy the type.
const ARTIFACT = {
  kind: "safe",
  preference: PREF,
  schema_version: 1,
} as unknown as RouteAlternative;

function mockFetchOnce(status: number, body: unknown) {
  const json = vi.fn().mockResolvedValue(body);
  const fetchMock = vi
    .fn()
    .mockResolvedValue({ ok: status < 400, status, json });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchReroute", () => {
  it("POSTs origin, destination and the carried preference to /reroute", async () => {
    const fetchMock = mockFetchOnce(200, { route: ARTIFACT });

    await fetchReroute(ORIGIN, DEST, PREF);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/reroute");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
    expect(JSON.parse(init.body)).toEqual({
      origin: ORIGIN,
      destination: DEST,
      preference: PREF,
    });
  });

  it("returns the single carried-level artifact from the response", async () => {
    mockFetchOnce(200, { route: ARTIFACT });

    const resp = await fetchReroute(ORIGIN, DEST, PREF);

    expect(resp.route).toEqual(ARTIFACT);
  });

  it("throws the server's detail message when the reroute fails", async () => {
    mockFetchOnce(422, { detail: "origin is outside the mapped area" });

    await expect(fetchReroute(ORIGIN, DEST, PREF)).rejects.toThrow(
      "origin is outside the mapped area",
    );
  });

  it("throws a status-coded fallback when there is no detail", async () => {
    mockFetchOnce(500, null);

    await expect(fetchReroute(ORIGIN, DEST, PREF)).rejects.toThrow(
      "reroute failed (500)",
    );
  });
});
