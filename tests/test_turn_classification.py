"""Turn classification: bearing wrap, maneuver types, U-turn legality."""
import numpy as np
import pytest

from ingestion.turns import classify_maneuver, wrap_delta_deg
from pyref.graph import Control, Maneuver
from tests.helpers.toy_graphs import GraphBuilder, find_edge


# ---------------------------------------------------------------- unit level
@pytest.mark.parametrize("delta,expected", [
    (0.0, Maneuver.STRAIGHT),
    (29.9, Maneuver.STRAIGHT),
    (-29.9, Maneuver.STRAIGHT),
    (30.0, Maneuver.RIGHT),
    (90.0, Maneuver.RIGHT),
    (150.0, Maneuver.RIGHT),
    (-30.0, Maneuver.LEFT),
    (-90.0, Maneuver.LEFT),
    (-150.0, Maneuver.LEFT),
    (150.1, Maneuver.UTURN),
    (-150.1, Maneuver.UTURN),
    (180.0, Maneuver.UTURN),
])
def test_classify_by_delta(delta, expected):
    assert classify_maneuver(delta, is_reverse_pair=False) == expected


def test_reverse_pair_forces_uturn():
    assert classify_maneuver(0.0, is_reverse_pair=True) == Maneuver.UTURN


@pytest.mark.parametrize("raw,wrapped", [
    (0, 0), (190, -170), (-190, 170), (360, 0), (540, 180), (-180, 180),
    # ±180 wrap: heading 350° then 10° is a 20° right drift, not a U-turn
    (10 - 350, 20),
])
def test_wrap_delta(raw, wrapped):
    assert wrap_delta_deg(raw) == pytest.approx(wrapped)


# ------------------------------------------------- via the real pipeline
def _cross() -> tuple:
    """4-way cross at the equator-ish; center node 0, N/S/E/W arms."""
    b = GraphBuilder()
    c = b.node(0.0, 0.0, control=Control.NONE)
    n = b.node(0.01, 0.0)
    s = b.node(-0.01, 0.0)
    e = b.node(0.0, 0.01)
    w = b.node(0.0, -0.01)
    for arm in (n, s, e, w):
        b.edge(c, arm, length_m=1000.0)
    return b.build(), c, n, s, e, w


def test_cross_maneuvers():
    pack, c, n, s, e, w = _cross()
    into_center = find_edge(pack, s, c)   # heading north
    lo, hi = pack.turn_ptr[into_center], pack.turn_ptr[into_center + 1]
    got = {}
    for t in range(lo, hi):
        out = pack.turn_out_edge[t]
        got[int(pack.edge_head[out])] = (Maneuver(pack.turn_maneuver[t]),
                                         int(pack.turn_allowed[t]))
    assert got[n] == (Maneuver.STRAIGHT, 1)
    assert got[w] == (Maneuver.LEFT, 1)
    assert got[e] == (Maneuver.RIGHT, 1)
    assert got[s] == (Maneuver.UTURN, 0)  # disallowed: not a dead end


def test_dead_end_uturn_allowed():
    b = GraphBuilder()
    a = b.node(0.0, 0.0)
    d = b.node(0.001, 0.0)
    b.edge(a, d, length_m=100.0)
    pack = b.build()
    into_dead_end = find_edge(pack, a, d)
    lo, hi = pack.turn_ptr[into_dead_end], pack.turn_ptr[into_dead_end + 1]
    assert hi - lo == 1
    assert Maneuver(pack.turn_maneuver[lo]) == Maneuver.UTURN
    assert pack.turn_allowed[lo] == 1


def test_turn_topology_consistency():
    pack, *_ = _cross()
    # every turn's out-edge departs from the in-edge's head
    assert np.all(pack.edge_tail[pack.turn_out_edge] ==
                  pack.edge_head[pack.turn_in_edge])
