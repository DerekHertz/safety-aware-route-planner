"use client";

import { useEffect, useRef, useState } from "react";
import { LatLon } from "./types";
import { distanceMeters } from "./useGeolocation";

/** Below this displacement, consecutive fixes are GPS jitter rather than
 *  real movement — the course between them is meaningless, so we hold the
 *  last known heading instead of spinning the camera in place. */
const MIN_DISPLACEMENT_M = 8;
/** Circular EMA smoothing factor for the raw fix-to-fix bearing. */
const SMOOTHING = 0.35;

function toRad(d: number): number {
  return (d * Math.PI) / 180;
}
function toDeg(r: number): number {
  return (r * 180) / Math.PI;
}

function bearingDeg(a: LatLon, b: LatLon): number {
  const y = Math.sin(toRad(b.lon - a.lon)) * Math.cos(toRad(b.lat));
  const x =
    Math.cos(toRad(a.lat)) * Math.sin(toRad(b.lat)) -
    Math.sin(toRad(a.lat)) *
      Math.cos(toRad(b.lat)) *
      Math.cos(toRad(b.lon - a.lon));
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

/** Exponential moving average over a circular quantity (degrees), taking the
 *  shortest angular path so it doesn't wrap the long way around at 0/360. */
function circularEma(prev: number | null, next: number, alpha: number): number {
  if (prev === null) return next;
  const diff = ((next - prev + 540) % 360) - 180;
  return (prev + alpha * diff + 360) % 360;
}

/**
 * GPS-course-derived heading: the bearing between consecutive fixes,
 * smoothed. Deliberately not device-compass-based (no extra permission
 * prompt, no iOS DeviceOrientationEvent dance) — see the navigation plan.
 *
 * Returns `null` until the first real movement is observed, and otherwise
 * holds the last smoothed heading while stationary rather than flipping to
 * null on every jittery fix.
 */
export function useHeading(position: LatLon | null): number | null {
  const [heading, setHeading] = useState<number | null>(null);
  const lastRef = useRef<LatLon | null>(null);

  useEffect(() => {
    if (!position) return;
    const last = lastRef.current;
    if (!last) {
      lastRef.current = position;
      return;
    }
    if (distanceMeters(last, position) < MIN_DISPLACEMENT_M) return;
    const brg = bearingDeg(last, position);
    lastRef.current = position;
    setHeading((prev) => circularEma(prev, brg, SMOOTHING));
  }, [position]);

  return heading;
}
