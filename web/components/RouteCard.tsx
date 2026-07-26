"use client";

import { KIND_COLORS } from "./MapView";
import { RouteAlternative } from "@/lib/types";

interface Props {
  route: RouteAlternative;
  selected: boolean;
  onSelect: () => void;
}

function fmtEta(s: number): string {
  const min = Math.round(s / 60);
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h ${min % 60} min`;
}

function fmtDist(m: number): string {
  return `${(m / 1000).toFixed(1)} km`;
}

export default function RouteCard({ route, selected, onSelect }: Props) {
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
        <span className="eta">{fmtEta(route.eta_s)}</span>
        <span className="dist">{fmtDist(route.distance_m)}</span>
      </div>
      <div className="route-card-unsafe">
        {u.total === 0 ? (
          <span className="unsafe-none">No flagged maneuvers</span>
        ) : (
          <>
            <span className="unsafe-count" title="Unprotected left turns onto busy streets">
              ⟲ {u.unprotected_left} unprotected left{u.unprotected_left === 1 ? "" : "s"}
            </span>
            <span className="unsafe-count" title="Uncontrolled crossings of busy streets">
              ✕ {u.uncontrolled_crossing} uncontrolled crossing{u.uncontrolled_crossing === 1 ? "" : "s"}
            </span>
          </>
        )}
      </div>
    </button>
  );
}
