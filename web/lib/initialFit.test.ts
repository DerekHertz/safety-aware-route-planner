import { describe, expect, it } from "vitest";
import {
  bottomOverlap,
  initialFitPadding,
  shouldRefitInitial,
} from "./initialFit";

const rect = (left: number, top: number, right: number, bottom: number) => ({
  left,
  top,
  right,
  bottom,
});

describe("bottomOverlap", () => {
  it("is the collapsed sheet's visible height when it floats over the map", () => {
    // Phone: map fills 375x812, sheet is 85dvh translated down to peek 158px.
    const map = rect(0, 0, 375, 812);
    const sheet = rect(0, 812 - 158, 375, 812 - 158 + 690);
    expect(bottomOverlap(map, sheet)).toBe(158);
  });

  it("is zero for a side column beside the map (desktop)", () => {
    const map = rect(380, 0, 1280, 800);
    const sidebar = rect(0, 0, 380, 800);
    expect(bottomOverlap(map, sidebar)).toBe(0);
  });

  it("is zero when the overlay sits entirely below the map", () => {
    expect(bottomOverlap(rect(0, 0, 400, 600), rect(0, 600, 400, 700))).toBe(0);
  });

  it("never exceeds the map's own height", () => {
    expect(bottomOverlap(rect(0, 100, 400, 600), rect(0, 0, 400, 900))).toBe(
      500,
    );
  });

  it("is zero with no overlay element", () => {
    expect(bottomOverlap(rect(0, 0, 400, 600), null)).toBe(0);
  });
});

describe("initialFitPadding", () => {
  it("clears the sheet on a phone, with a small side margin", () => {
    expect(initialFitPadding({ width: 375, height: 812 }, 158)).toEqual({
      top: 60,
      bottom: 158 + 24,
      left: 24,
      right: 24,
    });
  });

  it("uses the desktop route-fit inset when nothing covers the map", () => {
    expect(initialFitPadding({ width: 900, height: 800 }, 0)).toEqual({
      top: 60,
      bottom: 60,
      left: 60,
      right: 60,
    });
  });

  it("is null when the inset would not leave a usable map", () => {
    // The expanded sheet (85dvh) covers nearly everything: MapLibre would
    // refuse the fit, and there is nothing worth framing anyway.
    expect(initialFitPadding({ width: 375, height: 812 }, 690)).toBeNull();
    // A canvas that has not been sized yet.
    expect(initialFitPadding({ width: 0, height: 0 }, 0)).toBeNull();
    expect(initialFitPadding({ width: 100, height: 800 }, 0)).toBeNull();
  });
});

describe("shouldRefitInitial", () => {
  const ready = {
    done: false,
    hasBounds: true,
    cameraMoved: false,
    sized: true,
  };

  it("re-fits once the map is sized and nobody has moved the camera", () => {
    expect(shouldRefitInitial(ready)).toBe(true);
  });

  it("does it only once", () => {
    expect(shouldRefitInitial({ ...ready, done: true })).toBe(false);
  });

  it("leaves a camera the user or a GPS fix has already moved", () => {
    expect(shouldRefitInitial({ ...ready, cameraMoved: true })).toBe(false);
  });

  it("waits for a real size", () => {
    expect(shouldRefitInitial({ ...ready, sized: false })).toBe(false);
  });

  it("has nothing to re-fit without a bbox (the whole-world view)", () => {
    expect(shouldRefitInitial({ ...ready, hasBounds: false })).toBe(false);
  });
});
