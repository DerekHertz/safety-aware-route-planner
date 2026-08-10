"""route_maneuvers: turn-by-turn maneuver list, offsets cumulative along
route_geometry's exact coordinate sequence (see pyref/geometry.py)."""
from __future__ import annotations

import pytest

from pyref.geo import haversine_m
from pyref.geometry import route_maneuvers
from pyref.metrics import compute_metrics
from tests.helpers.fixtures import (
    make_costs,
    route_between_nodes,
    unprotected_left_city,
)
from tests.helpers.toy_graphs import GraphBuilder


def test_single_left_turn_offset_matches_first_edge_length():
    """fast route: S->A2, LEFT at A2 onto the arterial, then on to A0."""
    pack, ids = unprotected_left_city()
    qc = make_costs(pack)
    result = route_between_nodes(pack, qc, ids["s"], ids["a0"])
    assert result is not None

    maneuvers = route_maneuvers(pack, result)
    lefts = [m for m in maneuvers if m["type"] == "left"]
    assert len(maneuvers) == 1
    assert len(lefts) == 1

    s, a2 = ids["s"], ids["a2"]
    first_edge_len = haversine_m(pack.node_lat[s], pack.node_lon[s],
                                 pack.node_lat[a2], pack.node_lon[a2])
    assert lefts[0]["offset_m"] == pytest.approx(float(first_edge_len), abs=1.0)


def _zigzag():
    """A straight-line chain with no branching, so the shortest path is
    forced regardless of turn costs. Heading: N, E, N, E -> RIGHT, LEFT,
    RIGHT at n1, n2, n3 respectively. Each edge 1000 m."""
    b = GraphBuilder()
    n0 = b.node(0.0, 0.0)
    n1 = b.node(0.008, 0.0)
    n2 = b.node(0.008, 0.008)
    n3 = b.node(0.016, 0.008)
    n4 = b.node(0.016, 0.016)
    b.edge(n0, n1, length_m=1000.0)
    b.edge(n1, n2, length_m=1000.0)
    b.edge(n2, n3, length_m=1000.0)
    b.edge(n3, n4, length_m=1000.0)
    pack = b.build()
    return pack, dict(n0=n0, n1=n1, n2=n2, n3=n3, n4=n4)


def test_offsets_ascending_and_within_route_distance():
    pack, ids = _zigzag()
    qc = make_costs(pack)
    result = route_between_nodes(pack, qc, ids["n0"], ids["n4"])
    assert result is not None

    maneuvers = route_maneuvers(pack, result)
    # all three intermediate nodes are turns (none are STRAIGHT)
    assert len(maneuvers) == 3
    assert [m["type"] for m in maneuvers] == ["right", "left", "right"]

    # expected cumulative distance is the actual geodesic distance between
    # nodes (declared length_m is a routing-cost input, not the geometry) —
    # same quantity route_maneuvers accumulates from the geometry pool.
    expected = []
    seg_lens = [float(haversine_m(pack.node_lat[a], pack.node_lon[a],
                                  pack.node_lat[b], pack.node_lon[b]))
               for a, b in zip([ids["n0"], ids["n1"], ids["n2"]],
                               [ids["n1"], ids["n2"], ids["n3"]], strict=True)]
    cum = 0.0
    for seg in seg_lens:
        cum += seg
        expected.append(cum)

    offsets = [m["offset_m"] for m in maneuvers]
    assert offsets == sorted(offsets)
    for got, want in zip(offsets, expected, strict=True):
        assert got == pytest.approx(want, abs=1.0)

    metrics = compute_metrics(pack, qc, result)
    assert all(o < metrics.distance_m for o in offsets)


def test_straight_turns_are_filtered_out():
    # n0 -> n1 -> n2 is a single RIGHT turn; add a colinear continuation to
    # confirm a STRAIGHT turn produces no maneuver entry.
    b = GraphBuilder()
    a = b.node(0.0, 0.0)
    m = b.node(0.008, 0.0)
    c = b.node(0.016, 0.0)
    b.edge(a, m, length_m=1000.0)
    b.edge(m, c, length_m=1000.0)
    straight_pack = b.build()
    straight_qc = make_costs(straight_pack)
    result = route_between_nodes(straight_pack, straight_qc, a, c)
    assert result is not None
    maneuvers = route_maneuvers(straight_pack, result)
    assert maneuvers == []
