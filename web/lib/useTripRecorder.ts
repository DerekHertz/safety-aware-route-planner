"use client";

// Wires the trip-trace recorder (tripRecorder.ts, ADR-0017) to live
// navigation. Thin on purpose: every rule is in the recorder and the privacy
// module, and is tested there. This file only turns React changes into the
// recorder's events:
//
//   followed route null -> R      startTrip(R)
//   followed route R1 -> R2       reroute(R2)
//   phase -> "arrived"            endTrip(true)
//   followed route R -> null      endTrip(false), unless it already arrived
//   a new geolocation reading     addFix (the page's own watchPosition
//                                 stream; no second one is opened)

import { useEffect, useRef, useSyncExternalStore } from "react";
import type { NavPhase } from "./navigation";
import { createIdbTraceStorage } from "./traceStorage";
import {
  RecorderStatus,
  TripRecorder,
  createTripRecorder,
} from "./tripRecorder";
import type { LatLon, RouteAlternative } from "./types";
import type { GeoReading } from "./useGeolocation";

/** The commute planner service (ADR-0018). Unset, no recorder exists at all:
 *  no IndexedDB, no requests, no settings surface. NEXT_PUBLIC_* is inlined
 *  at build time. */
const COMMUTE_URL = process.env.NEXT_PUBLIC_COMMUTE_URL ?? "";

let singleton: TripRecorder | null | undefined;

/** This device's one recorder, created on first use; null where recording
 *  cannot happen (server render, no IndexedDB, no commute URL). */
export function getTripRecorder(): TripRecorder | null {
  if (singleton !== undefined) return singleton;
  if (typeof window === "undefined") return null;
  if (!COMMUTE_URL || typeof indexedDB === "undefined") {
    singleton = null;
    return null;
  }
  const rec = createTripRecorder({
    storage: createIdbTraceStorage(),
    baseUrl: COMMUTE_URL,
  });
  singleton = rec;
  // One tab records. A second tab left un-initialized stays inert, so it
  // cannot end the first tab's live trip as an orphan at its own launch.
  if ("locks" in navigator) {
    void navigator.locks.request(
      "sr-trip-recorder",
      { ifAvailable: true },
      (lock) => {
        if (!lock) return;
        void rec.init();
        return new Promise<void>(() => {}); // held for the page's lifetime
      },
    );
  } else {
    void rec.init();
  }
  window.addEventListener("online", () => void rec.retryNow());
  return rec;
}

const INERT: RecorderStatus = {
  configured: false,
  optedIn: false,
  label: null,
  enabled: false,
  rejected: false,
  recording: false,
  queued: 0,
  retryAt: null,
  error: null,
};

function subscribe(onChange: () => void): () => void {
  const rec = getTripRecorder();
  return rec ? rec.subscribe(onChange) : () => {};
}
const getSnapshot = () => (singleton ? singleton.getStatus() : INERT);
const getServerSnapshot = () => INERT;

/** The recorder's status, for the opt-in surface. */
export function useTripRecorderStatus(): RecorderStatus {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/**
 * Records the live-navigation session as a trip trace, when this device has
 * opted in. `route` is the FOLLOWED route while navigating (a reroute
 * replaces it) and null otherwise; `reading` is the latest geolocation fix.
 */
export function useTripRecorder(
  route: RouteAlternative | null,
  phase: NavPhase,
  destination: LatLon | null,
  reading: GeoReading | null,
): void {
  const prevRoute = useRef<RouteAlternative | null>(null);
  const ended = useRef(false);

  // Declared before the fix effect, so a session's first fix is recorded
  // after its trip starts (effects run in order; the recorder queues calls).
  useEffect(() => {
    const prev = prevRoute.current;
    if (prev === route) return;
    prevRoute.current = route;
    const rec = getTripRecorder();
    if (!rec) return;
    if (!prev && route) {
      ended.current = false;
      void rec.startTrip(route, destination);
    } else if (prev && route) {
      void rec.reroute(route);
    } else if (!ended.current) {
      ended.current = true;
      void rec.endTrip(false);
    }
  }, [route, destination]);

  useEffect(() => {
    if (phase !== "arrived" || !route || ended.current) return;
    ended.current = true;
    void getTripRecorder()?.endTrip(true);
  }, [phase, route]);

  useEffect(() => {
    if (!route || !reading) return;
    void getTripRecorder()?.addFix(reading);
  }, [route, reading]);
}
