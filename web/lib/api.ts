import {
  GeocodeResult,
  LatLon,
  PackMeta,
  RouteRequest,
  RouteResponse,
} from "./types";

// Default to the same-origin proxy (see the rewrite in next.config.ts) rather
// than an absolute localhost URL. Same-origin means no CORS, and — because
// NEXT_PUBLIC_* is inlined at build time — it means a tunnel handing out a new
// hostname needs no rebuild.
//
// Set NEXT_PUBLIC_API_URL to an absolute URL to bypass the proxy and talk to a
// deployed API directly.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api";

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
