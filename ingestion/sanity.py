"""Sanity report for a built pack: counts, histograms, defaulting rates."""
from __future__ import annotations

import numpy as np

from pyref.graph import Control, GraphPack, Maneuver, RoadClass


def report(pack: GraphPack) -> str:
    lines = []
    m = pack.meta
    lines.append(f"pack: {m.get('region')}  bbox={m.get('bbox')}")
    lines.append(f"nodes={pack.num_nodes:,}  directed_edges={pack.num_edges:,}  "
                 f"turns={pack.num_turns:,}  geom_points={len(pack.geom_lat):,}")

    total_km = pack.edge_length_m.sum() / 1000.0
    lines.append(f"total directed road length: {total_km:,.1f} km")

    def hist(name, values, enum):
        counts = np.bincount(values, minlength=len(enum))
        parts = [f"{e.name}={counts[e.value]:,}" for e in enum if counts[e.value]]
        lines.append(f"{name}: " + "  ".join(parts))

    hist("node controls", pack.node_control, Control)
    hist("edge road classes", pack.edge_road_class, RoadClass)
    hist("turn maneuvers", pack.turn_maneuver, Maneuver)

    two_way = int((pack.edge_reverse >= 0).sum())
    lines.append(f"two-way paired edges: {two_way:,} ({100.0 * two_way / max(pack.num_edges, 1):.1f}%)")
    lines.append(f"must-stop approaches: {int(pack.edge_must_stop.sum()):,}")
    lines.append(f"median-divided edges: {int(pack.edge_median.sum()):,}")
    lines.append(f"U-turn transitions allowed (dead ends): "
                 f"{int(((pack.turn_maneuver == Maneuver.UTURN) & (pack.turn_allowed == 1)).sum()):,}")
    lines.append(f"disallowed transitions: {int((pack.turn_allowed == 0).sum()):,}")
    stats = m.get("stats", {})
    lines.append(f"speed defaulted: {stats.get('pct_speed_defaulted')}%  "
                 f"lanes defaulted: {stats.get('pct_lanes_defaulted')}%")
    deg = np.diff(pack.node_out_ptr)
    lines.append("node out-degree histogram: " +
                 "  ".join(f"{d}:{int((deg == d).sum()):,}" for d in range(0, int(deg.max()) + 1)))
    return "\n".join(lines)
