"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LatLon } from "./types";

export interface GeoState {
  position: LatLon | null;
  accuracy: number | null;
  error: string | null;
  /** true once we've either got a fix or conclusively failed */
  settled: boolean;
}

// --- dev-only simulated GPS track --------------------------------------
//
// Real navigation can't be exercised on a desktop dev machine by walking
// around, and Chrome DevTools' geolocation override only offers a single
// static point. This lets the console (or an automated driver) feed a
// sequence of fixes on a timer instead, so camera-follow, off-route
// detection, turn-by-turn, and alerts can all be watched end-to-end.
// Stripped in production builds — never reachable outside `next dev`.
export interface MockGeoPoint extends LatLon {
  accuracy?: number;
}
export interface MockGeoOptions {
  /** Milliseconds between fixes. Default 1000. */
  intervalMs?: number;
  /** Restart from the first point after the last one. Default false. */
  loop?: boolean;
}
declare global {
  interface Window {
    __srMockGeo?: {
      start: (points: MockGeoPoint[], opts?: MockGeoOptions) => void;
      stop: () => void;
    };
  }
}
const MOCK_START_EVENT = "sr:mock-geo-start";
const MOCK_STOP_EVENT = "sr:mock-geo-stop";

if (process.env.NODE_ENV !== "production" && typeof window !== "undefined") {
  window.__srMockGeo ??= {
    start(points, opts) {
      window.dispatchEvent(
        new CustomEvent(MOCK_START_EVENT, { detail: { points, opts } }),
      );
    },
    stop() {
      window.dispatchEvent(new CustomEvent(MOCK_STOP_EVENT));
    },
  };
}

/**
 * Continuous position tracking via watchPosition (the user chose live
 * tracking over a one-shot fix).
 *
 * Note: the Geolocation API requires a secure context. `localhost` counts as
 * one, so dev works over plain http; a deployed build needs https.
 */
export function useGeolocation(
  enabled = true,
): GeoState & { refresh: () => void } {
  const [state, setState] = useState<GeoState>({
    position: null,
    accuracy: null,
    error: null,
    settled: false,
  });
  const watchId = useRef<number | null>(null);

  const onOk = useCallback((pos: GeolocationPosition) => {
    setState({
      position: { lat: pos.coords.latitude, lon: pos.coords.longitude },
      accuracy: pos.coords.accuracy,
      error: null,
      settled: true,
    });
  }, []);

  const onFail = useCallback((err: GeolocationPositionError) => {
    const msg =
      err.code === err.PERMISSION_DENIED
        ? "Location permission denied"
        : err.code === err.POSITION_UNAVAILABLE
          ? "Location unavailable"
          : err.code === err.TIMEOUT
            ? "Location request timed out"
            : "Location error";
    setState((s) => ({ ...s, error: msg, settled: true }));
  }, []);

  useEffect(() => {
    if (!enabled) return;
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setState((s) => ({
        ...s,
        error: "Geolocation not supported",
        settled: true,
      }));
      return;
    }
    watchId.current = navigator.geolocation.watchPosition(onOk, onFail, {
      enableHighAccuracy: true,
      maximumAge: 5000,
      timeout: 15000,
    });
    return () => {
      if (watchId.current !== null) {
        navigator.geolocation.clearWatch(watchId.current);
        watchId.current = null;
      }
    };
  }, [enabled, onOk, onFail]);

  // Dev-only: a mock track (started via window.__srMockGeo.start(...) from
  // the console) overrides real fixes on its own timer. See the module-level
  // comment above for why this exists.
  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    if (typeof window === "undefined") return;
    let timer: number | null = null;
    const clear = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const onStart = (ev: Event) => {
      clear();
      const { points, opts } = (ev as CustomEvent).detail as {
        points: MockGeoPoint[];
        opts?: MockGeoOptions;
      };
      if (!points?.length) return;
      const intervalMs = opts?.intervalMs ?? 1000;
      const loop = opts?.loop ?? false;
      let i = 0;
      const emit = () => {
        // Clamp the index: on a single-point (or just-exhausted) track the
        // end-of-track clear() below is a no-op on the first, synchronous emit
        // — `timer` isn't assigned until after emit() returns — so the interval
        // fires once more and would otherwise index past the end.
        const p = points[Math.min(i, points.length - 1)];
        setState({
          position: { lat: p.lat, lon: p.lon },
          accuracy: p.accuracy ?? 5,
          error: null,
          settled: true,
        });
        i += 1;
        if (i >= points.length) {
          if (loop) i = 0;
          else clear();
        }
      };
      emit();
      timer = window.setInterval(emit, intervalMs);
    };
    const onStop = () => clear();
    window.addEventListener(MOCK_START_EVENT, onStart);
    window.addEventListener(MOCK_STOP_EVENT, onStop);
    return () => {
      clear();
      window.removeEventListener(MOCK_START_EVENT, onStart);
      window.removeEventListener(MOCK_STOP_EVENT, onStop);
    };
  }, []);

  const refresh = useCallback(() => {
    if (typeof navigator === "undefined" || !navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(onOk, onFail, {
      enableHighAccuracy: true,
      maximumAge: 0,
      timeout: 15000,
    });
  }, [onOk, onFail]);

  return { ...state, refresh };
}

/** Metres between two points (haversine) — mirrors pyref/geo.py. */
export function distanceMeters(a: LatLon, b: LatLon): number {
  const R = 6_371_000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

/** bbox is [west, south, east, north]; null bbox means "no known coverage". */
export function insideBbox(p: LatLon, bbox: number[] | null): boolean {
  if (!bbox || bbox.length !== 4) return true;
  const [west, south, east, north] = bbox;
  return p.lon >= west && p.lon <= east && p.lat >= south && p.lat <= north;
}
