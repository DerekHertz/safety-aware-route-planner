"""Google Maps handoff (ADR-0020): pin waypoints so a time-optimal navigator,
asked to drive through them, follows our route's safety.

Google's own routing is unobservable here, so the pinning uses our lambda-0
search as its stand-in: from each anchor, a waypoint goes on the farthest edge
of our route that the time-fastest path still reaches without taking more
flagged maneuvers than our route does over the same stretch.
"""
import numpy as np
import pytest

from pyref.costs import arc_cost, heuristic
from pyref.handoff import pin_waypoints
from pyref.search import shortest_path, topo_of
from tests.helpers.fixtures import (
    grid3x3,
    make_quiet_costs,
    route_between_nodes,
    unprotected_left_city,
)
from tests.helpers.toy_graphs import find_edge

LAM_SAFE = 1.5


def _node_query(pack, qc, u, v):
    """Seeds/dests exactly as route_between_nodes builds them, plus the A*
    heuristic toward v, so pinning sees the same query the route came from."""
    seeds = [(int(e), float(qc.edge_time_s[e]))
             for e in np.flatnonzero(pack.edge_tail == u)]
    dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == v)]
    h = heuristic(pack, qc, float(pack.node_lat[v]), float(pack.node_lon[v]))
    return seeds, dests, h


def _pin(pack, qc, target, u, v, max_waypoints=9):
    seeds, dests, h = _node_query(pack, qc, u, v)
    topo = topo_of(pack)
    return pin_waypoints(
        pack, qc, target, seeds=seeds, dests=dests, h_dest=h,
        run=lambda ac, hh, s, d: shortest_path(topo, ac, hh, s, d),
        max_waypoints=max_waypoints)


def test_safe_route_gets_one_waypoint_before_the_protected_left():
    """The named scenario: fast turns left unprotected at A2; safe goes
    S->SW->A1. The time-fastest path reaches SW->A1 no less safely (it is
    the only way there), but not A1->A0, so one waypoint on SW->A1 makes a
    time-optimal navigator take the protected left at A1."""
    pack, ids = unprotected_left_city()
    qc = make_quiet_costs(pack)
    safe = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=LAM_SAFE)
    assert safe.edges(pack.turn_out_edge)[0] == find_edge(pack, ids["s"], ids["sw"])

    plan = _pin(pack, qc, safe, ids["s"], ids["a0"])

    assert len(plan.waypoints) == 1
    lat, lon = plan.waypoints[0]
    # midpoint of the SW->A1 block: SW (-0.004, -0.004), A1 (0.0, -0.004)
    assert lat == pytest.approx(-0.002, abs=1e-6)
    assert lon == pytest.approx(-0.004, abs=1e-9)
    assert plan.predicted_unsafe == 0


def test_fast_route_needs_no_waypoints():
    """A route the stand-in already takes gets no pins, and its predicted
    count is its own."""
    pack, ids = unprotected_left_city()
    qc = make_quiet_costs(pack)
    fast = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=0.0)

    plan = _pin(pack, qc, fast, ids["s"], ids["a0"])

    assert plan.waypoints == []
    assert plan.predicted_unsafe == 1


def test_waypoint_cap_reports_the_residual():
    """With no waypoints allowed, the handoff cannot steer the navigator, and
    says so: it predicts the unprotected left the stand-in takes."""
    pack, ids = unprotected_left_city()
    qc = make_quiet_costs(pack)
    safe = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=LAM_SAFE)

    plan = _pin(pack, qc, safe, ids["s"], ids["a0"], max_waypoints=0)

    assert plan.waypoints == []
    assert plan.predicted_unsafe == 1


def test_pins_lie_on_the_target_route():
    pack, ids = unprotected_left_city()
    qc = make_quiet_costs(pack)
    safe = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=LAM_SAFE)

    plan = _pin(pack, qc, safe, ids["s"], ids["a0"])

    assert plan.edges == [find_edge(pack, ids["sw"], ids["a1"])]
    assert set(plan.edges) <= set(safe.edges(pack.turn_out_edge))


def test_equally_safe_divergence_is_not_pinned():
    """Pins protect safety; they do not copy our path. On a flag-free grid,
    a target that differs from the stand-in's path gets no waypoints."""
    pack, nodes = grid3x3()
    qc = make_quiet_costs(pack)
    u, v = nodes[0], nodes[8]
    seeds, dests, h = _node_query(pack, qc, u, v)
    topo = topo_of(pack)
    fastest = shortest_path(topo, arc_cost(pack, qc, 0.0), h, seeds, dests)
    # Steer onto a different corner-to-corner path by inflating every turn
    # into the second edge the unconstrained search used.
    ac = arc_cost(pack, qc, 0.0).copy()
    ac[pack.turn_out_edge == fastest.edges(pack.turn_out_edge)[1]] *= 10.0
    other = shortest_path(topo, ac, h, seeds, dests)
    assert other.edges(pack.turn_out_edge) != fastest.edges(pack.turn_out_edge)

    plan = _pin(pack, qc, other, u, v)

    assert plan.waypoints == []
    assert plan.predicted_unsafe == 0
