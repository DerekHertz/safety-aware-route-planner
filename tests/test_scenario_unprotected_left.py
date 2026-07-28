"""THE named scenario test (spec): an intersection where the safe route
avoids an unprotected left the fast route takes — assert the metric
difference and that the routes actually diverge."""
import numpy as np
import pytest

from pyref.alternatives import compute_alternatives, jaccard
from pyref.config import Config
from pyref.graph import Maneuver
from pyref.metrics import compute_metrics
from pyref.search import topo_of
from tests.helpers.fixtures import (
    find_turn,
    make_costs,
    route_between_nodes,
    stop_sign_left_city,
    unprotected_left_city,
)
from tests.helpers.toy_graphs import find_edge

CFG = Config.load()


def test_fast_route_takes_the_unprotected_left():
    pack, ids = unprotected_left_city()
    qc = make_costs(pack)
    r = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=0.0)
    assert r.edges(pack.turn_out_edge) == [
        find_edge(pack, ids["s"], ids["a2"]),
        find_edge(pack, ids["a2"], ids["a1"]),
        find_edge(pack, ids["a1"], ids["a0"]),
    ]
    m = compute_metrics(pack, qc, r)
    assert m.unprotected_left == 1
    # 50 s side street + 2 x ~32.14 s arterial blocks
    assert m.eta_s == pytest.approx(50.0 + 2 * (500.0 / (56.0 / 3.6)), rel=1e-9)


def test_safe_route_detours_to_protected_signal():
    pack, ids = unprotected_left_city()
    qc = make_costs(pack)
    lam_safe = float(CFG["alternatives"]["lambda_safe"])
    r = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=lam_safe)
    assert r.edges(pack.turn_out_edge) == [
        find_edge(pack, ids["s"], ids["sw"]),
        find_edge(pack, ids["sw"], ids["a1"]),
        find_edge(pack, ids["a1"], ids["a0"]),
    ]
    m = compute_metrics(pack, qc, r)
    assert m.unprotected_left == 0
    assert m.uncontrolled_crossing == 0
    # the protected left itself carries zero penalty
    t_left = find_turn(pack,
                       find_edge(pack, ids["sw"], ids["a1"]),
                       find_edge(pack, ids["a1"], ids["a0"]))
    assert Maneuver(pack.turn_maneuver[t_left]) == Maneuver.LEFT
    assert qc.turn_penalty_s[t_left] == 0.0


def test_metric_difference_and_divergence():
    pack, ids = unprotected_left_city()
    qc = make_costs(pack)
    lam_safe = float(CFG["alternatives"]["lambda_safe"])
    fast = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=0.0)
    safe = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=lam_safe)
    mf = compute_metrics(pack, qc, fast)
    ms = compute_metrics(pack, qc, safe)
    # the safe route pays time to remove the unsafe maneuver
    assert ms.eta_s > mf.eta_s
    assert mf.unprotected_left == 1 and ms.unprotected_left == 0
    # and the two routes genuinely diverge
    fe = set(fast.edges(pack.turn_out_edge))
    se = set(safe.edges(pack.turn_out_edge))
    assert jaccard(fe, se) < float(CFG["alternatives"]["jaccard_threshold"])


def _alts(pack, qc, ids, **kwargs):
    seeds = [(int(e), float(qc.edge_time_s[e]))
             for e in np.flatnonzero(pack.edge_tail == ids["s"])]
    dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == ids["a0"])]
    return compute_alternatives(pack, qc, topo_of(pack), seeds, dests, None,
                                CFG, safety_enabled=True, **kwargs)


# ------------------------------------------------- the stop-sign left (ex_1)
def test_stop_sign_left_across_four_lanes_is_counted():
    """The maneuver that used to slip through entirely: control is STOP_2WAY,
    not NONE, so the old predicate ignored it — even though holding the stop
    sign is exactly what makes it dangerous."""
    pack, ids = stop_sign_left_city()
    qc = make_costs(pack)
    fast = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=0.0)
    assert fast.edges(pack.turn_out_edge) == [
        find_edge(pack, ids["s"], ids["a2"]),
        find_edge(pack, ids["a2"], ids["a1"]),
        find_edge(pack, ids["a1"], ids["a0"]),
    ]
    assert compute_metrics(pack, qc, fast).unprotected_left == 1


def test_safe_route_takes_the_left_at_the_light_instead():
    """And the alternative it routes to is the signalized intersection — the
    left there is discounted and uncounted, though still amber on the map."""
    pack, ids = stop_sign_left_city()
    qc = make_costs(pack)
    lam_safe = float(CFG["alternatives"]["lambda_safe"])
    safe = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=lam_safe)
    assert safe.edges(pack.turn_out_edge) == [
        find_edge(pack, ids["s"], ids["sw"]),
        find_edge(pack, ids["sw"], ids["a1"]),
        find_edge(pack, ids["a1"], ids["a0"]),
    ]
    m = compute_metrics(pack, qc, safe)
    assert m.unsafe_total == 0
    t_left = find_turn(pack,
                       find_edge(pack, ids["sw"], ids["a1"]),
                       find_edge(pack, ids["a1"], ids["a0"]))
    assert Maneuver(pack.turn_maneuver[t_left]) == Maneuver.LEFT
    assert qc.turn_penalty_s[t_left] > 0.0        # discounted, not exonerated


def test_detour_budget_forces_the_light_when_lambda_will_not_pay():
    """A long enough detour loses the lambda trade however high lambda goes.
    The budget is what makes "go over to the light" reliable: the hard-avoid
    run buys the same detour outright, as long as it fits the ceiling.

    Side streets of 1200 m make the safe route ~1.9x the fast one, well past
    what lambda_safe will trade for.
    """
    pack, ids = stop_sign_left_city(side_street_m=1200.0)
    qc = make_costs(pack)
    lam_safe = float(CFG["alternatives"]["lambda_safe"])

    swept = route_between_nodes(pack, qc, ids["s"], ids["a0"], lam=lam_safe)
    assert compute_metrics(pack, qc, swept).unprotected_left == 1

    stingy = _alts(pack, qc, ids, detour_budget_pct=0.25)
    assert all(compute_metrics(pack, qc, a.result).unsafe_total > 0
               or a.kind == "fast" for a in stingy)

    generous = _alts(pack, qc, ids, detour_budget_pct=1.5)
    by_kind = {a.kind: a for a in generous}
    assert compute_metrics(pack, qc, by_kind["safe"].result).unsafe_total == 0


def test_alternatives_api_labels_and_orders():
    pack, ids = unprotected_left_city()
    qc = make_costs(pack)
    seeds = [(int(e), float(qc.edge_time_s[e]))
             for e in np.flatnonzero(pack.edge_tail == ids["s"])]
    dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == ids["a0"])]
    alts = compute_alternatives(pack, qc, topo_of(pack), seeds, dests, None,
                                CFG, safety_enabled=True)
    by_kind = {a.kind: a for a in alts}
    assert "fast" in by_kind and "safe" in by_kind
    mf = compute_metrics(pack, qc, by_kind["fast"].result)
    ms = compute_metrics(pack, qc, by_kind["safe"].result)
    assert mf.eta_s < ms.eta_s
    assert mf.unprotected_left == 1
    assert ms.unsafe_total == 0
