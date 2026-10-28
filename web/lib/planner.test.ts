import { afterEach, describe, expect, it, vi } from "vitest";
import { PlannerAuthError, fetchPlan, relaxationLabel } from "./planner";
import { LatLon } from "./types";

const ORIGIN: LatLon = { lat: 37.87, lon: -122.27 };
const DEST: LatLon = { lat: 37.85, lon: -122.25 };

function mockFetchOnce(status: number, body: unknown) {
  const json = vi.fn().mockResolvedValue(body);
  const fetchMock = vi
    .fn()
    .mockResolvedValue({ ok: status < 400, status, json });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("fetchPlan", () => {
  it("posts the Request with the device clock and the bearer token", async () => {
    const fetchMock = mockFetchOnce(200, { status: "ok" });
    await fetchPlan(
      ORIGIN,
      DEST,
      "safest way",
      "2026-10-27T09:30",
      ["detour_budget"],
      "srt_x",
      "http://planner/",
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://planner/v1/plan");
    expect(init.headers.Authorization).toBe("Bearer srt_x");
    expect(JSON.parse(init.body)).toEqual({
      origin: ORIGIN,
      destination: DEST,
      request: "safest way",
      now_local: "2026-10-27T09:30",
      relax: ["detour_budget"],
    });
  });

  it("sends no Authorization header without a token", async () => {
    const fetchMock = mockFetchOnce(200, { status: "ok" });
    await fetchPlan(ORIGIN, DEST, "r", "t", [], null, "http://p");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("turns a 401 into a PlannerAuthError and other errors into their detail", async () => {
    mockFetchOnce(401, { detail: "missing bearer tester token" });
    await expect(
      fetchPlan(ORIGIN, DEST, "r", "t", [], null, "http://p"),
    ).rejects.toBeInstanceOf(PlannerAuthError);
    mockFetchOnce(503, { detail: "the Trip planner's daily budget is spent" });
    await expect(
      fetchPlan(ORIGIN, DEST, "r", "t", [], null, "http://p"),
    ).rejects.toThrow("daily budget");
  });
});

describe("relaxationLabel", () => {
  it("names the constraints in words", () => {
    expect(
      relaxationLabel(["no_uncontrolled_crossings", "detour_budget"]),
    ).toBe(
      "Allow no uncontrolled crossings and the detour limit as a preference",
    );
  });
});
