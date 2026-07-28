"""Dijkstra on hand-computed toy graphs (36 km/h = 10 m/s, 1000 m = 100 s)."""
import numpy as np
import pytest

from pyref.graph import Maneuver
from tests.helpers.fixtures import (
    grid3x3,
    line3,
    make_costs,
    route_between_nodes,
)
from tests.helpers.toy_graphs import find_edge


def test_line3_end_to_end():
    pack, a, m, c = line3()
    qc = make_costs(pack)
    r = route_between_nodes(pack, qc, a, c)
    assert r is not None
    assert r.total_cost == pytest.approx(200.0)
    assert r.edges(pack.turn_out_edge) == [find_edge(pack, a, m), find_edge(pack, m, c)]


def test_line3_single_edge():
    pack, a, m, c = line3()
    qc = make_costs(pack)
    r = route_between_nodes(pack, qc, a, m)
    assert r is not None
    assert r.total_cost == pytest.approx(100.0)
    assert len(r.turn_ids) == 0
    assert r.first_edge == r.dest_edge == find_edge(pack, a, m)


def test_grid_corner_to_corner():
    pack, nodes = grid3x3()
    qc = make_costs(pack)
    r = route_between_nodes(pack, qc, nodes[0], nodes[8])
    assert r is not None
    assert r.total_cost == pytest.approx(400.0)
    assert len(r.edges(pack.turn_out_edge)) == 4


def test_unreachable_returns_none():
    pack, nodes = grid3x3()
    qc = make_costs(pack)
    # destination with no in-edges at all
    r_empty = route_between_nodes(pack, qc, nodes[0], nodes[0])
    # routing to own node: dests = in-edges of origin; a loop back exists via
    # the grid, so this is actually reachable — assert it found SOMETHING sane
    assert r_empty is None or r_empty.total_cost > 0

    from pyref.costs import arc_cost
    from pyref.search import shortest_path, topo_of
    r = shortest_path(topo_of(pack), arc_cost(pack, qc, 0.0), None,
                      seeds=[(0, float(qc.edge_time_s[0]))], dests=[])
    assert r is None


def test_lambda_zero_matches_pure_time():
    """With lambda=0 the safety penalty must not influence cost at all."""
    pack, nodes = grid3x3()
    qc = make_costs(pack)
    r = route_between_nodes(pack, qc, nodes[0], nodes[5], lam=0.0)
    edges = r.edges(pack.turn_out_edge)
    assert r.total_cost == pytest.approx(float(np.sum(qc.edge_time_s[edges])))


def test_disallowed_uturn_forces_dead_end_detour():
    """Reversing direction mid-line: the U-turn at interior node m is
    disallowed, so the only legal reversal is driving on to the dead end c
    and turning around there — 3 extra edges instead of 1 forbidden U-turn."""
    pack, a, m, c = line3()
    qc = make_costs(pack)
    from pyref.costs import arc_cost
    from pyref.search import shortest_path, topo_of
    e_am = find_edge(pack, a, m)
    e_ma = find_edge(pack, m, a)
    r = shortest_path(topo_of(pack), arc_cost(pack, qc, 0.0), None,
                      seeds=[(e_am, 100.0)], dests=[(e_ma, 0.0)])
    assert r is not None
    assert r.total_cost == pytest.approx(400.0)  # a->m + m->c + c->m + m->a
    assert r.edges(pack.turn_out_edge) == [
        e_am, find_edge(pack, m, c), find_edge(pack, c, m), e_ma]


def test_dead_end_uturn_taken_when_needed():
    pack, a, m, c = line3()
    qc = make_costs(pack)
    from pyref.costs import arc_cost
    from pyref.search import shortest_path, topo_of
    e_mc = find_edge(pack, m, c)
    e_cm = find_edge(pack, c, m)
    # from m->c, come back c->m: c IS a dead end, U-turn allowed
    r = shortest_path(topo_of(pack), arc_cost(pack, qc, 0.0), None,
                      seeds=[(e_mc, 100.0)], dests=[(e_cm, 0.0)])
    assert r is not None
    assert len(r.turn_ids) == 1
    assert Maneuver(pack.turn_maneuver[r.turn_ids[0]]) == Maneuver.UTURN
