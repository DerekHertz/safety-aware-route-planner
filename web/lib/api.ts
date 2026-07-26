import { GeocodeResult, LatLon, RouteResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchRoutes(
  origin: LatLon,
  destination: LatLon,
  departureTime: string | null,
  safetyEnabled: boolean,
): Promise<RouteResponse> {
  const resp = await fetch(`${API_BASE}/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      origin,
      destination,
      departure_time: departureTime,
      safety_enabled: safetyEnabled,
    }),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail?.detail ?? `routing failed (${resp.status})`);
  }
  return resp.json();
}

export async function geocode(q: string): Promise<GeocodeResult[]> {
  const resp = await fetch(`${API_BASE}/geocode?q=${encodeURIComponent(q)}`);
  if (!resp.ok) throw new Error("geocoding failed");
  const data = await resp.json();
  return data.results;
}
