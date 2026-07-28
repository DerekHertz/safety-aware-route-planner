"""A* correctness: admissibility (A* cost == Dijkstra cost) and consistency
of the heuristic (h(e) <= time-only arc cost + h(e2) for every allowed turn)."""
import numpy as np
import pytest

from pyref.costs import arc_cost, heuristic
from tests.helpers.fixtures import grid3x3, line3, make_costs, route_between_nodes


@pytest.mark.parametrize("lam", [0.0, 0.5, 1.5])
def test_astar_matches_dijkstra_cost(lam):
    for pack, pairs in _cases():
        qc = make_costs(pack)
        for u, v in pairs:
            rd = route_between_nodes(pack, qc, u, v, lam=lam, algo="dijkstra")
            ra = route_between_nodes(pack, qc, u, v, lam=lam, algo="astar")
            assert (rd is None) == (ra is None)
            if rd is not None:
                # Identical generalized cost. The PATHS may differ: on graphs
                # with cost ties A* and Dijkstra settle states in different
                # orders, and both tie-broken optima are valid. (Determinism/
                # path parity is engine-to-engine for the SAME algorithm —
                # covered by the C++ parity suite — not algorithm-to-algorithm.)
                assert ra.total_cost == rd.total_cost


def _cases():
    pack1, a, m, c = line3()
    pack2, nodes = grid3x3()
    return [
        (pack1, [(a, c), (a, m), (c, a)]),
        (pack2, [(nodes[0], nodes[8]), (nodes[2], nodes[6]), (nodes[4], nodes[1])]),
    ]


@pytest.mark.parametrize("lam", [0.0, 1.5])
def test_heuristic_consistency_on_toys(lam):
    """h(e) <= arc_cost(t) + h(out(t)) for every allowed turn (consistency),
    which also implies admissibility along any path. Penalties only ADD cost,
    so consistency at lam=0 extends to all lam >= 0 — asserted empirically."""
    for pack, pairs in _cases():
        qc = make_costs(pack)
        for _u, v in pairs:
            h = heuristic(pack, qc, float(pack.node_lat[v]), float(pack.node_lon[v]))
            ac = arc_cost(pack, qc, lam)
            inn = pack.turn_in_edge
            out = pack.turn_out_edge
            allowed = pack.turn_allowed == 1
            slack = 1e-9  # float rounding headroom
            assert np.all(h[inn[allowed]] <= ac[allowed] + h[out[allowed]] + slack)


def test_heuristic_zero_at_destination_edges():
    pack, a, m, c = line3()
    qc = make_costs(pack)
    h = heuristic(pack, qc, float(pack.node_lat[c]), float(pack.node_lon[c]))
    dest_edges = np.flatnonzero(pack.edge_head == c)
    assert np.all(h[dest_edges] == 0.0)
