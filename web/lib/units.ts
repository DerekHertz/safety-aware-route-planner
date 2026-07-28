// Presentation-only unit conversion. The API always speaks SI (distance_m,
// eta_s) so the response contract stays framework-agnostic for a future
// mobile client — conversion happens here, at the edge.

export type UnitSystem = "imperial" | "metric";

export const UNITS_STORAGE_KEY = "sr.units";
export const DEFAULT_UNITS: UnitSystem = "imperial";

const METERS_PER_MILE = 1609.344;
const METERS_PER_FOOT = 0.3048;

/** Distance for display: feet below ~0.1 mi, metres below 1 km. */
export function formatDistance(meters: number, system: UnitSystem): string {
  if (!Number.isFinite(meters)) return "—";
  if (system === "imperial") {
    const miles = meters / METERS_PER_MILE;
    if (miles < 0.1)
      return `${Math.round(meters / METERS_PER_FOOT / 10) * 10} ft`;
    return `${miles.toFixed(miles < 10 ? 1 : 0)} mi`;
  }
  if (meters < 1000) return `${Math.round(meters / 10) * 10} m`;
  const km = meters / 1000;
  return `${km.toFixed(km < 10 ? 1 : 0)} km`;
}

/** Duration is unit-system independent. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  const min = Math.round(seconds / 60);
  if (min < 1) return "<1 min";
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h ${min % 60} min`;
}

export function isUnitSystem(v: unknown): v is UnitSystem {
  return v === "imperial" || v === "metric";
}
