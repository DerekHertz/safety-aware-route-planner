"""Explain how one intersection was classified.

    python scripts/inspect_junction.py --lat 37.8715 --lon -122.2680

Prints the nearest junction in the pack, the control governing each approach,
whether that control was OBSERVED (an OSM tag said so) or INFERRED (the
road-class heuristic guessed), and the maneuvers the cost model would flag.

Use it to check a specific intersection you saw flagged in the browser: an
approach reading `SIGNAL_PERMISSIVE / OBSERVED` means a real traffic_signals
node was found on the junction or one of its arms.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from pyref.config import DEFAULT_CONFIG_PATH, Config
from pyref.costs import (
    UNSAFE_CROSSING,
    UNSAFE_LEFT,
    compute_costs,
)
from pyref.geo import haversine_m
from pyref.graph import Control, ControlConfidence, GraphPack, Maneuver, RoadClass
from sim.snapshot import free_flow

_UNSAFE_LABEL = {UNSAFE_LEFT: "UNPROTECTED LEFT", UNSAFE_CROSSING: "UNCONTROLLED CROSSING"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    ap.add_argument("--pack", default=None, help="pack dir (default: data/packs/<active region>)")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    args = ap.parse_args()

    cfg = Config.load(args.config)
    pack_dir = Path(args.pack) if args.pack else Path("data/packs") / cfg.region_name
    pack = GraphPack.load(pack_dir)
    qc = compute_costs(pack, free_flow(pack, cfg), cfg)

    d = haversine_m(pack.node_lat, pack.node_lon, args.lat, args.lon)
    node = int(np.argmin(d))
    print(f"nearest junction: node {node}  osm={int(pack.node_osmid[node])}  "
          f"{pack.node_lat[node]:.6f},{pack.node_lon[node]:.6f}  "
          f"({d[node]:.0f} m from the query point)")
    print(f"  https://www.openstreetmap.org/node/{int(pack.node_osmid[node])}")
    print(f"  node-level control: {Control(int(pack.node_control[node])).name}")

    in_edges = np.flatnonzero(pack.edge_head == node)
    print(f"\n{len(in_edges)} approach(es):")
    for e in in_edges:
        ctrl = Control(int(pack.edge_approach_control[e]))
        conf = ControlConfidence(int(pack.edge_control_confidence[e]))
        rc = RoadClass(int(pack.edge_road_class[e])).name
        tail = int(pack.edge_tail[e])
        busy = "busy" if qc.edge_busy[e] else "quiet"
        major = " major" if qc.edge_major[e] else ""
        print(f"\n  from node {tail:>6} via {rc} "
              f"({pack.edge_lanes[e]:.0f} lane/dir, {busy}{major})")
        print(f"    control  : {ctrl.name}  [{conf.name}]"
              f"{'  must_stop' if pack.edge_must_stop[e] else ''}")
        lo, hi = int(pack.turn_ptr[e]), int(pack.turn_ptr[e + 1])
        for t in range(lo, hi):
            if not pack.turn_allowed[t]:
                continue
            out = int(pack.turn_out_edge[t])
            kind = _UNSAFE_LABEL.get(int(qc.turn_unsafe_type[t]), "")
            print(f"    {Maneuver(int(pack.turn_maneuver[t])).name:<8} "
                  f"-> node {int(pack.edge_head[out]):>6}  "
                  f"raw={qc.turn_raw[t]:5.2f}  penalty={qc.turn_penalty_s[t]:6.1f}s"
                  f"  {kind}")


if __name__ == "__main__":
    main()
