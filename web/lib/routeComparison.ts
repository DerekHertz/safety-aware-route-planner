// Fast-vs-safe comparison logic (handoff Phase 2). Zero backend: one /route
// response already carries all three alternatives with UnsafeCounts,
// per-segment tiers and detour_pct — this module just makes the trade-off
// ("is the safer route worth the extra minutes?") legible, without touching
// the wire contract.

import { RouteAlternative, RouteKind } from "./types";

/** Canonical display order for the three safety LEVELS. The response's own
 *  `routes` order isn't part of the frozen contract, so this reorders
 *  defensively rather than trusting array position — the same assumption
 *  MapView's ROUTE_KINDS already makes for the map legend. */
export const ROUTE_KIND_ORDER: RouteKind[] = ["fast", "balanced", "safe"];

export interface ComparisonRow {
  route: RouteAlternative;
  /** Seconds slower than the quickest alternative in this SAME response; 0
   *  for that alternative itself. The number this screen exists to show —
   *  `detour_pct` answers the same question as a fraction, this answers it in
   *  minutes, which is what a person actually weighs against fewer unsafe
   *  maneuvers. */
  deltaS: number;
  isFastest: boolean;
}

/** Orders whichever alternatives the response contains as fast/balanced/safe
 *  and works out each one's time cost against the fastest of the set. Missing
 *  kinds (e.g. `safety_enabled: false` returns just "fast") are dropped
 *  rather than backfilled. */
export function compareRoutes(routes: RouteAlternative[]): ComparisonRow[] {
  if (routes.length === 0) return [];
  const fastestEtaS = Math.min(...routes.map((r) => r.eta_s));
  return ROUTE_KIND_ORDER.map((kind) => routes.find((r) => r.kind === kind))
    .filter((r): r is RouteAlternative => r != null)
    .map((route) => ({
      route,
      deltaS: route.eta_s - fastestEtaS,
      isFastest: route.eta_s === fastestEtaS,
    }));
}

/** "fastest" rather than "+0 min" for the quickest route(s); "+<1 min" rather
 *  than rounding a real-but-small delta down into the fastest label. */
export function formatTimeDelta(deltaS: number): string {
  if (deltaS <= 0) return "fastest";
  const min = Math.round(deltaS / 60);
  if (min < 1) return "+<1 min";
  return `+${min} min`;
}
