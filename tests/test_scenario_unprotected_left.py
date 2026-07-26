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
    find_turn, make_costs, route_between_nodes, unprotected_left_city,
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
