"""Lambda sweep, Jaccard dedup and penalty-method rerun."""
import numpy as np
import pytest

from pyref.alternatives import compute_alternatives, jaccard
from pyref.search import topo_of
from tests.helpers.fixtures import grid3x3, make_costs, unprotected_left_city


def test_jaccard():
    assert jaccard({1, 2, 3}, {1, 2, 3}) == 1.0
    assert jaccard({1, 2}, {3, 4}) == 0.0
    assert jaccard({1, 2, 3}, {2, 3, 4}) == pytest.approx(0.5)
    assert jaccard(set(), set()) == 1.0


def _node_query(pack, qc, u, v):
    seeds = [(int(e), float(qc.edge_time_s[e]))
             for e in np.flatnonzero(pack.edge_tail == u)]
    dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == v)]
    return seeds, dests


def test_safety_off_returns_single_fast(monkeypatch):
    pack, ids = unprotected_left_city()
    qc = make_costs(pack)
    from pyref.config import Config
    cfg = Config.load()
    seeds, dests = _node_query(pack, qc, ids["s"], ids["a0"])
    alts = compute_alternatives(pack, qc, topo_of(pack), seeds, dests, None,
                                cfg, safety_enabled=False)
    assert [a.kind for a in alts] == ["fast"]


def test_sweep_produces_distinct_fast_and_safe():
    pack, ids = unprotected_left_city()
    qc = make_costs(pack)
    from pyref.config import Config
    cfg = Config.load()
    seeds, dests = _node_query(pack, qc, ids["s"], ids["a0"])
    alts = compute_alternatives(pack, qc, topo_of(pack), seeds, dests, None,
                                cfg, safety_enabled=True)
    kinds = {a.kind: a for a in alts}
    assert "fast" in kinds and "safe" in kinds
    fast_edges = set(kinds["fast"].result.edges(pack.turn_out_edge))
    safe_edges = set(kinds["safe"].result.edges(pack.turn_out_edge))
    threshold = float(cfg["alternatives"]["jaccard_threshold"])
    assert jaccard(fast_edges, safe_edges) <= threshold


def test_dedup_drops_or_diversifies_duplicates():
    """On a uniform grid all lambdas give equal-cost routes; every kept
    alternative must be pairwise distinct under the Jaccard threshold."""
    pack, nodes = grid3x3()
    qc = make_costs(pack)
    from pyref.config import Config
    cfg = Config.load()
    seeds, dests = _node_query(pack, qc, nodes[0], nodes[8])
    alts = compute_alternatives(pack, qc, topo_of(pack), seeds, dests, None,
                                cfg, safety_enabled=True)
    assert 1 <= len(alts) <= 3
    threshold = float(cfg["alternatives"]["jaccard_threshold"])
    sets = [set(a.result.edges(pack.turn_out_edge)) for a in alts]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert jaccard(sets[i], sets[j]) <= threshold
