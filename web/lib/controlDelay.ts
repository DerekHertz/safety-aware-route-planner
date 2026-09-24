// Display text for control delay (ADR-0016): the expected time spent waiting
// at an intersection for a signal, an all-way stop, or a usable gap in
// traffic. It is TIME, not a safety score, and it is already inside `eta_s` —
// so nothing here may present it as extra time on top of the ETA.
//
// Two wire fields feed it: `RouteAlternative.control_delay_s` (the whole
// route's total, for the route cards) and `UnsafePoint.expected_wait_s` (one
// flagged maneuver, for its map popup).

import { UnsafeType } from "./types";
import { formatDuration } from "./units";

/** A wait as a short approximate duration: "~45 s", "~3 min", or "<5 s".
 *
 *  Seconds below a minute, because a single maneuver's wait usually IS below
 *  a minute and "<1 min" would say nothing; rounded to 5 s, because the
 *  constants behind it are uncalibrated guesses (ADR-0017). A minute and up
 *  goes through `formatDuration`, so it reads like the ETA it is part of.
 *
 *  Null when there is no honest number to show — notably a server that
 *  predates ADR-0016 and omits the field. */
export function formatWait(seconds: number): string | null {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const s = Math.round(seconds / 5) * 5;
  if (s === 0) return "<5 s";
  if (s < 60) return `~${s} s`;
  return `~${formatDuration(seconds)}`;
}

/** A route card's waiting line: "~3 min waiting". Null hides it. */
export function routeWaitLabel(controlDelayS: number): string | null {
  const wait = formatWait(controlDelayS);
  if (wait === null) return null;
  if (controlDelayS === 0) return "no waits expected";
  return `${wait} waiting`;
}

export interface UnsafePointPopup {
  title: string;
  /** Null when the wait is unknown: the popup shows the title alone. */
  wait: string | null;
}

/** What an unsafe-point marker's popup says: which maneuver, and how long
 *  the driver can expect to wait there. */
export function unsafePointPopup(
  type: UnsafeType,
  expectedWaitS: number,
): UnsafePointPopup {
  const title =
    type === "unprotected_left"
      ? "Unprotected left turn onto a busy street"
      : "Uncontrolled crossing of a busy street";
  const wait = formatWait(expectedWaitS);
  return {
    title,
    wait:
      wait === null
        ? null
        : expectedWaitS === 0
          ? "No wait expected"
          : `Expected wait: ${wait}`,
  };
}
