import {
  GeocodeResult,
  LatLon,
  PackMeta,
  RouteRequest,
  RouteResponse,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchRoutes(
  origin: LatLon,
  destination: LatLon,
  departureTime: string | null,
  safetyEnabled: boolean,
  detourBudgetPct: number,
): Promise<RouteResponse> {
  // Typed rather than an inline object literal, so the schema-sync check has
  // something to compare the request side of the contract against.
  const body: RouteRequest = {
    origin,
    destination,
    departure_time: departureTime,
    safety_enabled: safetyEnabled,
    detour_budget_pct: detourBudgetPct,
  };
  const resp = await fetch(`${API_BASE}/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail?.detail ?? `routing failed (${resp.status})`);
  }
  return resp.json();
}

export async function fetchMeta(): Promise<PackMeta> {
  const resp = await fetch(`${API_BASE}/meta`);
  if (!resp.ok) throw new Error("failed to load region metadata");
  return resp.json();
}

export async function geocode(q: string): Promise<GeocodeResult[]> {
  const resp = await fetch(`${API_BASE}/geocode?q=${encodeURIComponent(q)}`);
  if (!resp.ok) throw new Error("geocoding failed");
  const data = await resp.json();
  return data.results;
}
