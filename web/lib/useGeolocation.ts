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
