import { describe, expect, it } from "vitest";
import { formatWait, routeWaitLabel, unsafePointPopup } from "./controlDelay";

describe("formatWait", () => {
  it("rounds a sub-minute wait to the nearest 5 s", () => {
    expect(formatWait(44)).toBe("~45 s");
    expect(formatWait(12.4)).toBe("~10 s");
    expect(formatWait(57.4)).toBe("~55 s");
  });

  it("says <5 s rather than ~0 s for a real-but-tiny wait", () => {
    // The 3 am case (ADR-0016): about a second of waiting for a gap.
    expect(formatWait(1.1)).toBe("<5 s");
    expect(formatWait(0)).toBe("<5 s");
    expect(formatWait(2.5)).toBe("~5 s");
  });

  it("switches to whole minutes instead of printing ~60 s", () => {
    expect(formatWait(57.5)).toBe("~1 min");
    expect(formatWait(89)).toBe("~1 min");
    expect(formatWait(90)).toBe("~2 min");
    // A permissive left at a signal can reach 142 s (handoff Phase 4b (1)).
    expect(formatWait(142)).toBe("~2 min");
  });

  it("uses formatDuration's hours past an hour", () => {
    expect(formatWait(3900)).toBe("~1 h 5 min");
  });

  it("returns null when there is no honest number to show", () => {
    // A server that predates ADR-0016 omits the field entirely.
    expect(formatWait(undefined as unknown as number)).toBeNull();
    expect(formatWait(NaN)).toBeNull();
    expect(formatWait(Infinity)).toBeNull();
    expect(formatWait(-1)).toBeNull();
  });
});

describe("routeWaitLabel", () => {
  it("reads as the time spent waiting, never as extra time", () => {
    expect(routeWaitLabel(180)).toBe("~3 min waiting");
    expect(routeWaitLabel(44)).toBe("~45 s waiting");
    expect(routeWaitLabel(1)).toBe("<5 s waiting");
  });

  it("says so when the route has no waits at all", () => {
    expect(routeWaitLabel(0)).toBe("no waits expected");
  });

  it("is null when the field is missing or invalid, so the card omits it", () => {
    expect(routeWaitLabel(undefined as unknown as number)).toBeNull();
    expect(routeWaitLabel(-5)).toBeNull();
  });
});

describe("unsafePointPopup", () => {
  it("names the maneuver by type", () => {
    expect(unsafePointPopup("unprotected_left", 30).title).toBe(
      "Unprotected left turn onto a busy street",
    );
    expect(unsafePointPopup("uncontrolled_crossing", 30).title).toBe(
      "Uncontrolled crossing of a busy street",
    );
  });

  it("gives the expected wait at that one maneuver", () => {
    expect(unsafePointPopup("uncontrolled_crossing", 63).wait).toBe(
      "Expected wait: ~1 min",
    );
    expect(unsafePointPopup("unprotected_left", 22).wait).toBe(
      "Expected wait: ~20 s",
    );
  });

  it("says no wait rather than <5 s for an exact zero", () => {
    expect(unsafePointPopup("unprotected_left", 0).wait).toBe(
      "No wait expected",
    );
  });

  it("omits the wait line when the value is missing", () => {
    // MapLibre hands feature properties back untyped; a missing property
    // arrives as NaN once coerced.
    expect(unsafePointPopup("unprotected_left", NaN).wait).toBeNull();
  });
});
