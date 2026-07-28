"""Assemble a GraphPack from an OSMnx MultiDiGraph.

Determinism: nodes are sorted by OSM id; directed edges are sorted by
(tail_index, head_index, way_id, multigraph_key). All arrays are created with
explicit dtypes (f64/i64/i32/u8) — never platform-default ints.
"""
from __future__ import annotations

import datetime

import networkx as nx
import numpy as np

from ingestion import controls, turns
from ingestion.enrich import (
    effective_lanes,
    effective_speed_kph,
    infer_median,
    is_oneway,
    road_class_from_highway,
)
from pyref.config import Config
from pyref.graph import (
    ROAD_CLASS_RANK,
    Control,
    ControlConfidence,
    GraphPack,
    RoadClass,
)

KPH_TO_MPS = 1.0 / 3.6


def build_pack(G: nx.MultiDiGraph, cfg: Config, region: str | None = None,
               observed_controls: dict | None = None) -> GraphPack:
    """Assemble the pack.

    `observed_controls` maps (junction_osmid, from_osmid) -> ApproachControl,
    as produced by ingestion.approach_controls.harvest(). Approaches it covers
    are marked OBSERVED; the rest fall back to the road-class heuristic in
    controls.py and are marked INFERRED. Passing None (toy graphs, tests that
    build their own topology) makes every approach INFERRED.
    """
    region = region or cfg.region_name
    observed_controls = observed_controls or {}

    # ---------------------------------------------------------------- nodes
    node_ids = sorted(G.nodes)
    node_index = {osmid: i for i, osmid in enumerate(node_ids)}
    N = len(node_ids)
    node_lat = np.array([G.nodes[n]["y"] for n in node_ids], dtype=np.float64)
    node_lon = np.array([G.nodes[n]["x"] for n in node_ids], dtype=np.float64)
    node_osmid = np.array(node_ids, dtype=np.int64)

    # ------------------------------------------------------- directed edges
    def way_id(data) -> int:
        osmid = data.get("osmid", 0)
        if isinstance(osmid, (list, tuple)):
            return int(min(osmid))
        return int(osmid)

    raw_edges = [(node_index[u], node_index[v], way_id(d), k, d)
                 for u, v, k, d in G.edges(keys=True, data=True)]
    raw_edges.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    E = len(raw_edges)

    edge_tail = np.array([t[0] for t in raw_edges], dtype=np.int32)
    edge_head = np.array([t[1] for t in raw_edges], dtype=np.int32)
    edge_osm_way = np.array([t[2] for t in raw_edges], dtype=np.int64)

    edge_length_m = np.zeros(E, dtype=np.float64)
    edge_speed_mps = np.zeros(E, dtype=np.float64)
    edge_lanes = np.zeros(E, dtype=np.float64)
    edge_median = np.zeros(E, dtype=np.uint8)
    edge_road_class = np.zeros(E, dtype=np.uint8)
    speed_defaulted = np.zeros(E, dtype=bool)   # sanity-report bookkeeping
    lanes_defaulted = np.zeros(E, dtype=bool)

    geom_lat_pool: list[float] = []
    geom_lon_pool: list[float] = []
    edge_geom_ptr = np.zeros(E + 1, dtype=np.int32)

    for e, (ti, hi, _, _, data) in enumerate(raw_edges):
        rc = road_class_from_highway(data.get("highway", "unclassified"))
        ow = is_oneway(data)
        edge_road_class[e] = int(rc)
        edge_length_m[e] = float(data["length"])
        edge_speed_mps[e] = effective_speed_kph(data, rc, cfg) * KPH_TO_MPS
        edge_lanes[e] = effective_lanes(data, rc, ow, cfg)
        edge_median[e] = 1 if infer_median(rc, ow) else 0
        speed_defaulted[e] = "maxspeed" not in data
        lanes_defaulted[e] = "lanes" not in data

        geom = data.get("geometry")
        if geom is not None:
            lons, lats = zip(*geom.coords, strict=True)  # shapely: (x=lon, y=lat)
        else:
            lats = (node_lat[ti], node_lat[hi])
            lons = (node_lon[ti], node_lon[hi])
        geom_lat_pool.extend(lats)
        geom_lon_pool.extend(lons)
        edge_geom_ptr[e + 1] = len(geom_lat_pool)

    geom_lat = np.array(geom_lat_pool, dtype=np.float64)
    geom_lon = np.array(geom_lon_pool, dtype=np.float64)

    # ------------------------------------------------------------- node CSR
    node_out_ptr = np.zeros(N + 1, dtype=np.int32)
    np.add.at(node_out_ptr, edge_tail + 1, 1)
    node_out_ptr = np.cumsum(node_out_ptr, dtype=np.int64).astype(np.int32)

    # ---------------------------------------------------------- reverse map
    # Opposite direction of a two-way street: edge (v,u) sharing the way id.
    edge_reverse = np.full(E, -1, dtype=np.int32)
    by_endpoints: dict[tuple[int, int, int], list[int]] = {}
    for e in range(E):
        by_endpoints.setdefault(
            (int(edge_tail[e]), int(edge_head[e]), int(edge_osm_way[e])), []
        ).append(e)
    for e in range(E):
        if edge_reverse[e] != -1:
            continue
        cands = by_endpoints.get(
            (int(edge_head[e]), int(edge_tail[e]), int(edge_osm_way[e])), []
        )
        for e2 in cands:
            if e2 != e and edge_reverse[e2] == -1:
                edge_reverse[e] = e2
                edge_reverse[e2] = e
                break

    # ------------------------------------------------------------- controls
    in_edges_by_node: list[list[int]] = [[] for _ in range(N)]
    for e in range(E):
        in_edges_by_node[edge_head[e]].append(e)

    node_control = np.zeros(N, dtype=np.uint8)
    edge_approach_control = np.zeros(E, dtype=np.uint8)
    edge_must_stop = np.zeros(E, dtype=np.uint8)
    edge_control_confidence = np.full(E, int(ControlConfidence.INFERRED), dtype=np.uint8)

    for i, osmid in enumerate(node_ids):
        preds = set(G.predecessors(osmid))
        succs = set(G.successors(osmid))
        num_legs = len((preds | succs) - {osmid})
        incident_data = [d for *_, d in G.in_edges(osmid, data=True)] + \
                        [d for *_, d in G.out_edges(osmid, data=True)]
        approach_ranks = [
            ROAD_CLASS_RANK[RoadClass(int(edge_road_class[e]))]
            for e in in_edges_by_node[i]
        ]
        ctrl, ctrl_conf = controls.classify_node_control(
            G.nodes[osmid], incident_data, num_legs, approach_ranks
        )
        observed_here = [observed_controls.get((osmid, int(node_osmid[edge_tail[e]])))
                         for e in in_edges_by_node[i]]
        # A roundabout is recognised from the `junction` tag on the incident
        # WAYS, which survives simplification — the approach walk only reads
        # node tags and cannot see it. An edge-tagged roundabout therefore keeps
        # precedence over any stop or give_way node harvested on its approaches.
        if ctrl == Control.ROUNDABOUT:
            observed_here = [None] * len(observed_here)
        # node_control is a debug/reporting summary; the cost model reads
        # edge_approach_control. Report the harvested control when every
        # approach agrees on one, else keep the node-level classification.
        seen = {a.control for a in observed_here if a is not None}
        node_control[i] = int(seen.pop()) if len(seen) == 1 else int(ctrl)

        for e, observed in zip(in_edges_by_node[i], observed_here, strict=True):
            if observed is not None:
                edge_approach_control[e] = int(observed.control)
                edge_must_stop[e] = 1 if observed.must_stop else 0
                edge_control_confidence[e] = int(observed.confidence)
                continue
            rank = ROAD_CLASS_RANK[RoadClass(int(edge_road_class[e]))]
            ac, must = controls.resolve_approach(ctrl, rank, approach_ranks)
            edge_approach_control[e] = int(ac)
            edge_must_stop[e] = 1 if must else 0
            edge_control_confidence[e] = int(ctrl_conf)

    # ----------------------------------------------------------- turn table
    bearing_in, bearing_out = turns.edge_bearings(geom_lat, geom_lon, edge_geom_ptr)
    turn_ptr, turn_out_edge, turn_maneuver, turn_angle_deg, turn_allowed = \
        turns.build_turn_table(edge_tail, edge_head, edge_reverse,
                               node_out_ptr, bearing_in, bearing_out)

    try:
        bbox = list(cfg.bbox(region))
    except KeyError:      # toy/test graphs have no bbox preset
        bbox = None
    meta = {
        "region": region,
        "bbox": bbox,
        "network_type": cfg.network_type,
        "config_hash": cfg.content_hash(),
        "created_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "stats": {
            "pct_speed_defaulted": round(100.0 * float(speed_defaulted.mean()), 1) if E else 0.0,
            "pct_lanes_defaulted": round(100.0 * float(lanes_defaulted.mean()), 1) if E else 0.0,
            "pct_control_observed": round(
                100.0 * float((edge_control_confidence
                               == int(ControlConfidence.OBSERVED)).mean()), 1) if E else 0.0,
        },
    }

    return GraphPack(
        node_lat=node_lat, node_lon=node_lon, node_osmid=node_osmid,
        node_control=node_control,
        node_out_ptr=node_out_ptr,
        edge_tail=edge_tail, edge_head=edge_head,
        edge_length_m=edge_length_m, edge_speed_mps=edge_speed_mps,
        edge_lanes=edge_lanes, edge_median=edge_median,
        edge_road_class=edge_road_class, edge_reverse=edge_reverse,
        edge_bearing_in=bearing_in, edge_bearing_out=bearing_out,
        edge_approach_control=edge_approach_control,
        edge_must_stop=edge_must_stop,
        edge_control_confidence=edge_control_confidence,
        edge_osm_way=edge_osm_way,
        edge_geom_ptr=edge_geom_ptr, geom_lat=geom_lat, geom_lon=geom_lon,
        turn_ptr=turn_ptr, turn_out_edge=turn_out_edge,
        turn_maneuver=turn_maneuver, turn_angle_deg=turn_angle_deg,
        turn_allowed=turn_allowed,
        meta=meta,
    )
