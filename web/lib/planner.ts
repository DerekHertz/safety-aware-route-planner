// The Trip planner client (ADR-0019): `POST /v1/plan` on its own service.
//
// Off unless NEXT_PUBLIC_PLANNER_URL is set (inlined at build time), so a
// deployment without the planner shows the app exactly as before. The service
// needs a tester token in production; it is kept on this device only.
import { ConstraintName, LatLon, PlanRequest, PlanResponse } from "./types";

export const PLANNER_URL = process.env.NEXT_PUBLIC_PLANNER_URL ?? "";
export const PLANNER_TOKEN_KEY = "planner.token";

export class PlannerAuthError extends Error {}

export async function fetchPlan(
  origin: LatLon,
  destination: LatLon,
  request: string,
  nowLocal: string,
  relax: ConstraintName[],
  token: string | null,
  base: string = PLANNER_URL,
): Promise<PlanResponse> {
  const body: PlanRequest = {
    origin,
    destination,
    request,
    now_local: nowLocal,
    relax,
  };
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(`${base.replace(/\/$/, "")}/v1/plan`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (resp.status === 401) {
    throw new PlannerAuthError("The Trip planner needs a tester token.");
  }
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(detail?.detail ?? `planning failed (${resp.status})`);
  }
  return resp.json();
}

const CONSTRAINT_LABELS: Record<ConstraintName, string> = {
  no_unprotected_lefts: "no unprotected left turns",
  no_uncontrolled_crossings: "no uncontrolled crossings",
  detour_budget: "the detour limit",
};

/** The button text that confirms a Relaxation. */
export function relaxationLabel(constraints: ConstraintName[]): string {
  return `Allow ${constraints.map((c) => CONSTRAINT_LABELS[c]).join(" and ")} as a preference`;
}

export function readToken(): string | null {
  try {
    return localStorage.getItem(PLANNER_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string): void {
  try {
    localStorage.setItem(PLANNER_TOKEN_KEY, token);
  } catch {
    // private mode: the token lasts for this page only
  }
}
