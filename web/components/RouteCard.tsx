"use client";

import { KIND_COLORS } from "./MapView";
import { formatTimeDelta } from "@/lib/routeComparison";
import { RouteAlternative } from "@/lib/types";
import { UnitSystem, formatDistance, formatDuration } from "@/lib/units";

interface Props {
  route: RouteAlternative;
  /** Seconds slower than the fastest alternative in the SAME response — see
   *  lib/routeComparison. Optional so a lone route (e.g. safety disabled) can
   *  render without a comparison to make. */
  deltaS?: number;
  units: UnitSystem;
  selected: boolean;
  onSelect: () => void;
}

export default function RouteCard({
  route,
  deltaS,
  units,
  selected,
  onSelect,
}: Props) {
  const color = KIND_COLORS[route.kind];
  const u = route.unsafe;
  return (
    <button
      type="button"
      className={`route-card${selected ? " selected" : ""}`}
      style={{ borderLeftColor: color }}
      onClick={onSelect}
    >
      <div className="route-card-head">
        <span className="kind-badge" style={{ background: color }}>
          {route.kind}
        </span>
        <span className="eta">{formatDuration(route.eta_s)}</span>
        {deltaS !== undefined && (
          <span
            className={`time-delta${deltaS <= 0 ? " fastest" : ""}`}
            title="Time versus the fastest alternative in this comparison"
          >
            {formatTimeDelta(deltaS)}
          </span>
        )}
        <span className="dist">{formatDistance(route.distance_m, units)}</span>
      </div>
      <div className="route-card-unsafe">
        {u.total === 0 && (
          <span className="unsafe-none">No flagged maneuvers</span>
        )}
        {u.unprotected_left > 0 && (
          <span
            className="unsafe-count"
            title="Left turns onto a busy street with nothing holding back the traffic you cross"
          >
            ⟲ {u.unprotected_left} unprotected left
            {u.unprotected_left === 1 ? "" : "s"}
          </span>
        )}
        {u.uncontrolled_crossing > 0 && (
          <span
            className="unsafe-count"
            title="Crossings of a busy street with nothing holding back the traffic you cross"
          >
            ✕ {u.uncontrolled_crossing} uncontrolled crossing
            {u.uncontrolled_crossing === 1 ? "" : "s"}
          </span>
        )}
        {route.detour_pct > 0.005 && (
          <span
            className="detour-cost"
            title="Extra time versus the fastest route"
          >
            +{Math.round(route.detour_pct * 100)}% longer
          </span>
        )}
      </div>
    </button>
  );
}
