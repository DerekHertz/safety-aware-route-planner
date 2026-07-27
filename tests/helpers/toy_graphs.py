"""GraphBuilder: hand-built toy graphs that run through the REAL ingestion
pipeline (ingestion.pack.build_pack), so tests exercise production turn
classification, control resolution and pack assembly — not a parallel
reimplementation.

Use round lengths (explicit length_m) and speeds like 36 km/h (10 m/s) so
optimal routes are hand-computable exactly.
"""
from __future__ import annotations

import networkx as nx

from ingestion.pack import build_pack
from pyref.config import Config
from pyref.geo import haversine_m
from pyref.graph import Control, GraphPack, RoadClass


class GraphBuilder:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config.load()
        self.G = nx.MultiDiGraph()
        self._next_node = 0
        self._next_way = 100

    def node(self, lat: float, lon: float,
             control: Control | None = None, **tags) -> int:
        """Add a node.

        control=None leaves the node untagged so the production inference
        heuristic decides (yielding ControlConfidence.INFERRED); an explicit
        Control pins it via the control_override hook (OBSERVED). Extra
        keyword arguments become raw OSM node tags — use them to place real
        `highway=stop` / `highway=traffic_signals` nodes.
        """
        nid = self._next_node
        self._next_node += 1
        attrs = {"y": lat, "x": lon, **tags}
        if control is not None:
            attrs["control_override"] = control.name
        self.G.add_node(nid, **attrs)
        return nid

    def edge(self, u: int, v: int, *,
             road_class: RoadClass = RoadClass.residential,
             speed_kph: float = 36.0,   # 10 m/s
             lanes: float = 1.0,        # per direction
             oneway: bool = False,
             length_m: float | None = None,
             median: bool | None = None) -> None:
        """Add a road segment (both directions unless oneway)."""
        if length_m is None:
            length_m = float(haversine_m(self.G.nodes[u]["y"], self.G.nodes[u]["x"],
                                         self.G.nodes[v]["y"], self.G.nodes[v]["x"]))
        way = self._next_way
        self._next_way += 1
        # OSM `lanes` counts both directions on two-way ways; the production
        # parser halves it, so feed it the total.
        lanes_tag = lanes if oneway else lanes * 2
        attrs = dict(osmid=way, highway=road_class.name, length=length_m,
                     maxspeed=f"{speed_kph:g}", lanes=f"{lanes_tag:g}",
                     oneway=oneway)
        self.G.add_edge(u, v, **attrs)
        if not oneway:
            self.G.add_edge(v, u, **attrs)
        if median is not None:
            # infer_median() is a heuristic on (class, oneway); tests that
            # need an explicit median override patch the built arrays instead.
            self._median_overrides = getattr(self, "_median_overrides", [])
            self._median_overrides.append((u, v, median))

    def build(self, region: str = "toy", observed_controls=None) -> GraphPack:
        pack = build_pack(self.G, self.cfg, region,
                          observed_controls=observed_controls)
        for (u, v, median) in getattr(self, "_median_overrides", []):
            import numpy as np
            mask = ((pack.edge_tail == u) & (pack.edge_head == v)) | \
                   ((pack.edge_tail == v) & (pack.edge_head == u))
            pack.edge_median[mask] = 1 if median else 0
        return pack


def find_edge(pack: GraphPack, tail: int, head: int) -> int:
    """Directed edge index between two builder node ids (which coincide with
    pack node indices because builder ids are 0..N-1 and sorted)."""
    import numpy as np
    hits = np.flatnonzero((pack.edge_tail == tail) & (pack.edge_head == head))
    assert len(hits) == 1, f"expected exactly one edge {tail}->{head}, got {len(hits)}"
    return int(hits[0])
