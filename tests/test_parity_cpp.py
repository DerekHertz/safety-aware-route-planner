"""Python <-> C++ parity: identical turn-id paths and BITWISE-equal costs.

The shared-precompute design (all FP math in numpy, engines do single
additions in fixed order) makes bitwise equality the expectation, not a
stretch goal. Path equality is asserted FIRST so a tie-break violation
surfaces as a path diff rather than a silent cost match.

Set SR_PARITY_LOOSE=1 to relax the cost assertion to 1e-9 while diagnosing
a regression; the committed default is exact.
"""
import os

import numpy as np
import pytest

sr_core = pytest.importorskip("sr_core")

from pyref.config import Config
from pyref.costs import arc_cost, compute_costs, heuristic
from pyref.graph import GraphPack
from pyref.search import shortest_path, topo_of
from sim.snapshot import free_flow
from tests.helpers.fixtures import (
    cross_with_control,
    grid3x3,
    line3,
    make_costs,
    unprotected_left_city,
)

LOOSE = os.environ.get("SR_PARITY_LOOSE") == "1"
LAMBDAS = [0.0, 0.5, 1.5]


def _engine(pack):
    return sr_core.Engine(pack.turn_ptr, pack.turn_out_edge,
                          pack.turn_in_edge, pack.turn_allowed)


def _assert_parity(pack, qc, seeds, dests, lam, use_astar, dest_pt=None):
    ac = arc_cost(pack, qc, lam)
    h = None
    if use_astar:
        h = heuristic(pack, qc, dest_pt[0], dest_pt[1])
    r_py = shortest_path(topo_of(pack), ac, h, seeds, dests)
    hit = _engine(pack).shortest_path(ac, h, seeds, dests)

    if r_py is None:
        assert hit is None
        return
    assert hit is not None
    turns_cpp, first_cpp, dest_cpp, cost_cpp = hit
    assert list(turns_cpp) == list(r_py.turn_ids)          # path first
    assert first_cpp == r_py.first_edge
    assert dest_cpp == r_py.dest_edge
    if LOOSE:
        assert cost_cpp == pytest.approx(r_py.total_cost, abs=1e-9)
    else:
        assert cost_cpp == r_py.total_cost                  # bitwise


def _od_pairs(pack, num_nodes, rng, n=3):
    pairs = []
    for _ in range(n):
        u, v = rng.integers(0, num_nodes, size=2)
        pairs.append((int(u), int(v)))
    return pairs


def _node_query(pack, qc, u, v):
    seeds = [(int(e), float(qc.edge_time_s[e]))
             for e in np.flatnonzero(pack.edge_tail == u)]
    dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == v)]
    return seeds, dests


@pytest.mark.parametrize("use_astar", [False, True], ids=["dijkstra", "astar"])
@pytest.mark.parametrize("lam", LAMBDAS)
def test_parity_toy_fixtures(lam, use_astar):
    from pyref.graph import Control
    fixtures = [line3()[0], grid3x3()[0], unprotected_left_city()[0],
                cross_with_control(Control.SIGNAL_PERMISSIVE)[0]]
    rng = np.random.default_rng(42)
    for pack in fixtures:
        qc = make_costs(pack)
        for u, v in _od_pairs(pack, pack.num_nodes, rng):
            if u == v:
                continue
            seeds, dests = _node_query(pack, qc, u, v)
            dest_pt = (float(pack.node_lat[v]), float(pack.node_lon[v]))
            _assert_parity(pack, qc, seeds, dests, lam, use_astar, dest_pt)


REAL_PACK = "data/packs/berkeley_small"


@pytest.mark.skipif(not os.path.isdir(REAL_PACK),
                    reason="real pack not built")
@pytest.mark.parametrize("use_astar", [False, True], ids=["dijkstra", "astar"])
def test_parity_real_pack(use_astar):
    cfg = Config.load()
    pack = GraphPack.load(REAL_PACK)
    qc = compute_costs(pack, free_flow(pack, cfg), cfg)
    rng = np.random.default_rng(42)
    checked = 0
    for u, v in _od_pairs(pack, pack.num_nodes, rng, n=50):
        if u == v:
            continue
        seeds, dests = _node_query(pack, qc, u, v)
        if not seeds or not dests:
            continue
        for lam in LAMBDAS:
            dest_pt = (float(pack.node_lat[v]), float(pack.node_lon[v]))
            _assert_parity(pack, qc, seeds, dests, lam, use_astar, dest_pt)
        checked += 1
    assert checked >= 40  # the pack is connected enough for most pairs
