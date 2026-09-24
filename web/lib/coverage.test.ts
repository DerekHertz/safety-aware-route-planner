import { describe, expect, it } from "vitest";
import {
  bboxContains,
  bboxToLngLatBounds,
  coverageLabel,
  initialViewBbox,
  insideCoverage,
  normalizeMeta,
  packForPoint,
  preflight,
} from "./coverage";
import { ServedPack } from "./types";

// Two disjoint metros, [west, south, east, north] — the server's order. Kept
// far from each other and from (0, 0) so a swapped lat/lon can never land
// inside either by accident.
const A: ServedPack = {
  region: "metro_a",
  bbox: [-122.35, 37.8, -122.2, 37.9],
  num_edges: 100,
};
const B: ServedPack = {
  region: "new_york",
  bbox: [-74.05, 40.6, -73.9, 40.8],
  num_edges: 200,
};
const PACKS = [A, B];

const IN_A = { lat: 37.85, lon: -122.27 };
const IN_B = { lat: 40.7, lon: -74.0 };
const NOWHERE = { lat: 10, lon: 10 };

describe("bboxContains", () => {
  it("is closed on every edge and corner, like the server", () => {
    const [west, south, east, north] = A.bbox!;
    for (const lat of [south, north]) {
      for (const lon of [west, east]) {
        expect(bboxContains(A.bbox, { lat, lon })).toBe(true);
      }
    }
    expect(bboxContains(A.bbox, { lat: north + 1e-9, lon: east })).toBe(false);
    expect(bboxContains(A.bbox, { lat: south, lon: west - 1e-9 })).toBe(false);
  });

  it("reads bbox as [west, south, east, north] and points as lat/lon", () => {
    // The same numbers with lat and lon swapped are nowhere near Berkeley.
    expect(bboxContains(A.bbox, IN_A)).toBe(true);
    expect(bboxContains(A.bbox, { lat: IN_A.lon, lon: IN_A.lat })).toBe(false);
  });

  it("treats a null bbox as containing everything", () => {
    expect(bboxContains(null, NOWHERE)).toBe(true);
  });
});

describe("packForPoint", () => {
  it("finds the pack whose bbox contains the point", () => {
    expect(packForPoint(PACKS, IN_A)).toBe(A);
    expect(packForPoint(PACKS, IN_B)).toBe(B);
  });

  it("is null outside every pack", () => {
    expect(packForPoint(PACKS, NOWHERE)).toBeNull();
    expect(packForPoint([], IN_A)).toBeNull();
  });

  it("a sole null-bbox pack owns every point", () => {
    const toy = { region: "toy", bbox: null, num_edges: 3 };
    expect(packForPoint([toy], NOWHERE)).toBe(toy);
  });
});

describe("insideCoverage", () => {
  it("is inside ANY served pack, not just the default", () => {
    expect(insideCoverage(PACKS, IN_A)).toBe(true);
    expect(insideCoverage(PACKS, IN_B)).toBe(true);
    expect(insideCoverage(PACKS, NOWHERE)).toBe(false);
  });
});

describe("initialViewBbox", () => {
  it("frames the pack containing the first GPS fix", () => {
    expect(initialViewBbox(PACKS, IN_B)).toEqual(B.bbox);
  });

  it("falls back to the default (first) pack without a fix", () => {
    expect(initialViewBbox(PACKS, null)).toEqual(A.bbox);
  });

  it("falls back to the default pack for a fix outside coverage", () => {
    expect(initialViewBbox(PACKS, NOWHERE)).toEqual(A.bbox);
  });

  it("is null when there is nothing to frame", () => {
    expect(initialViewBbox([], IN_A)).toBeNull();
    expect(
      initialViewBbox([{ region: "toy", bbox: null, num_edges: 3 }], IN_A),
    ).toBeNull();
  });
});

describe("bboxToLngLatBounds", () => {
  it("turns [west, south, east, north] into MapLibre's [[lng, lat], [lng, lat]]", () => {
    expect(bboxToLngLatBounds([-122.35, 37.8, -122.2, 37.9])).toEqual([
      [-122.35, 37.8],
      [-122.2, 37.9],
    ]);
  });
});

describe("preflight", () => {
  it("passes when both endpoints are in the same pack", () => {
    expect(preflight(PACKS, IN_A, { lat: 37.88, lon: -122.3 })).toBeNull();
    expect(preflight(PACKS, IN_B, IN_B)).toBeNull();
  });

  it("refuses endpoints in different packs, naming both", () => {
    const msg = preflight(PACKS, IN_A, IN_B);
    expect(msg).toMatch(/different regions/);
    expect(msg).toContain("metro a");
    expect(msg).toContain("new york");
  });

  it("reports the origin first when both are outside, as the server does", () => {
    expect(preflight(PACKS, NOWHERE, NOWHERE)).toMatch(/starting point/);
  });

  it("reports a destination outside every pack", () => {
    const msg = preflight(PACKS, IN_A, NOWHERE);
    expect(msg).toMatch(/destination/);
    expect(msg).toContain(coverageLabel(PACKS));
  });

  it("never refuses on a sole null-bbox pack: the server decides by snapping", () => {
    const toy = { region: "toy", bbox: null, num_edges: 3 };
    expect(preflight([toy], NOWHERE, IN_B)).toBeNull();
  });

  it("does not guess with no pack list at all", () => {
    expect(preflight([], IN_A, IN_B)).toBeNull();
  });
});

describe("coverageLabel", () => {
  it("lists every served region, human-readably, in served order", () => {
    expect(coverageLabel(PACKS)).toBe("metro a, new york");
  });
});

describe("normalizeMeta", () => {
  it("keeps a server's packs list as-is", () => {
    const meta = {
      region: "metro_a",
      bbox: A.bbox,
      num_edges: 100,
      packs: PACKS,
    };
    expect(normalizeMeta(meta).packs).toBe(PACKS);
  });

  it("synthesizes the one pack from the top-level fields of an old server", () => {
    const old = { region: "metro_a", bbox: A.bbox, num_edges: 100 };
    expect(normalizeMeta(old)).toEqual({ ...old, packs: [A] });
  });

  it("carries a null bbox through the synthesized pack", () => {
    const old = { region: "toy", bbox: null, num_edges: 3 };
    expect(normalizeMeta(old).packs).toEqual([old]);
  });
});
