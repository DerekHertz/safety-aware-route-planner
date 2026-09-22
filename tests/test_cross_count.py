"""The uncontrolled-crossing predicate, pinned against its original formulation.

Phase 1(3a-next) (`docs/agents/handoff.md`, ADR-0009's 2026-09-20 later
amendment) replaced `_cross_count` -- a node-level `np.add.at` histogram run
twice per request over every edge and turn -- with a gather over only the few
percent of turns whose result is ever read. The two must agree *exactly*: the
output is a boolean mask, so there is no ulp to hide in, and any disagreement
is a changed unsafe count on some route.

`tests/test_costs_golden.py` pins the toy corpus bitwise but digests no real
pack, so on its own it would not notice a disagreement that only a 20k-edge
graph reaches (a node of in-degree 6, say). This file holds the original
algorithm as an oracle and compares on both, over many random masks as well as
real snapshots: `compute_costs` only ever feeds it masks where major implies
busy, and a rewrite that leaned on that without saying so should fail here.
"""
from __future__ import annotations

import dataclasses
import datetime

import numpy as np
import pytest

from pyref.config import Config
from pyref.costs import (
    PackStatics,
    _uncontrolled_crossing,
    build_pack_statics,
    compute_costs,
)
from pyref.graph import Control, GraphPack, Maneuver, RoadClass
from sim.snapshot import at_time, free_flow
from tests.helpers.fixtures import (
    cross_with_control,
    grid3x3,
    line3,
    stop_sign_left_city,
    unprotected_left_city,
)
from tests.helpers.packs import real_pack, skip_reason

CFG = Config.load()
REAL_PACKS = ["berkeley_oakland", "berkeley_small"]


def _oracle_cross_count(pack: GraphPack, mask: np.ndarray) -> np.ndarray:
    """The pre-3a-next `_cross_count`, verbatim in effect: does any OTHER
    incoming approach at the node match `mask`, excluding our own in-edge and
    the reverse of our out-edge?"""
    inn = pack.turn_in_edge
    rev = pack.edge_reverse[pack.turn_out_edge]
    has_rev = rev >= 0
    node_in = np.zeros(pack.num_nodes, dtype=np.int64)
    np.add.at(node_in, pack.edge_head, mask.astype(np.int64))
    count = node_in[pack.edge_head[inn]] - mask[inn].astype(np.int64)
    count = count - np.where(has_rev, mask[np.where(has_rev, rev, 0)]
                             .astype(np.int64), 0)
    return count > 0


def _oracle(pack: GraphPack, st: PackStatics, over_tau: np.ndarray,
            edge_busy: np.ndarray, edge_major: np.ndarray) -> np.ndarray:
    return (st.is_straight & st.observed & over_tau
            & ((st.ctrl_none & _oracle_cross_count(pack, edge_busy))
               | (st.unprotected_approach
                  & _oracle_cross_count(pack, edge_major))))


def _check(pack: GraphPack, seed: int, trials: int) -> None:
    st = build_pack_statics(pack, CFG)
    rng = np.random.default_rng(seed)
    E, T = len(pack.edge_head), pack.num_turns
    for i in range(trials):
        # Sweep density so both "almost nothing busy" and "almost everything
        # busy" are reached, and draw major independently of busy half the
        # time rather than only as a subset of it.
        p = (i + 0.5) / trials
        busy = rng.random(E) < p
        major = (rng.random(E) < p) if i % 2 else (busy & (rng.random(E) < 0.7))
        over_tau = rng.random(T) < 0.9
        got = _uncontrolled_crossing(st, over_tau, busy, major)
        want = _oracle(pack, st, over_tau, busy, major)
        assert got.dtype == np.bool_ and got.shape == (T,)
        assert np.array_equal(got, want), (
            f"trial {i}: {int((got != want).sum())} turns disagree")
    # Degenerate masks: the all-false and all-true extremes.
    for val in (False, True):
        m = np.full(E, val)
        ot = np.ones(T, dtype=bool)
        assert np.array_equal(_uncontrolled_crossing(st, ot, m, m),
                              _oracle(pack, st, ot, m, m))


def _toys():
    yield line3()[0]
    yield grid3x3()[0]
    yield unprotected_left_city()[0]
    yield stop_sign_left_city()[0]
    yield cross_with_control(None)[0]
    for ctrl in Control:
        yield cross_with_control(ctrl)[0]
    # Busy but not major: the one toy where the two masks differ.
    yield cross_with_control(Control.NONE, lanes=1,
                             road_class=RoadClass.residential)[0]


def test_matches_oracle_on_toys():
    for k, pack in enumerate(_toys()):
        _check(pack, seed=k, trials=40)


@pytest.mark.parametrize("name", REAL_PACKS)
def test_matches_oracle_on_real_pack(name):
    path = real_pack(name)
    if path is None:
        pytest.skip(skip_reason(name))
    _check(GraphPack.load(path), seed=7, trials=24)


@pytest.mark.parametrize("name", REAL_PACKS)
def test_unsafe_type_unchanged_on_real_snapshots(name):
    """End to end through `compute_costs`, at the departures that actually
    move the busy masks: the crossing type it emits is the oracle's."""
    path = real_pack(name)
    if path is None:
        pytest.skip(skip_reason(name))
    pack = GraphPack.load(path)
    st = build_pack_statics(pack, CFG)
    snaps = [free_flow(pack, CFG)] + [
        at_time(pack, CFG, datetime.datetime(2026, 7, 22, h, 30))
        for h in (3, 8, 12, 17, 22)]
    for snap in snaps:
        qc = compute_costs(pack, snap, CFG, st)
        over_tau = qc.turn_raw >= float(CFG["tiers"]["tau_unsafe"])
        want = _oracle(pack, st, over_tau, qc.edge_busy, qc.edge_major)
        got = qc.turn_unsafe_type == 2
        # A left can overwrite a crossing ("left wins"); straight turns are
        # never lefts, so on straights the two must agree exactly.
        assert np.array_equal(got, want & ~(qc.turn_unsafe_type == 1))
        assert want.any(), "snapshot reaches no crossing; the check is vacuous"


def test_statics_reject_a_straight_that_reverses_its_in_edge():
    """The rewrite relies on the two excluded legs being distinct edges. The
    ingestion classifier guarantees it (out == reverse(in) is forced to UTURN,
    `ingestion/turns.py`); a hand-built pack that violated it must fail loudly
    at load time rather than silently change the count."""
    pack = cross_with_control(None)[0]
    reverses = np.flatnonzero(
        pack.edge_reverse[pack.turn_out_edge] == pack.turn_in_edge)
    assert len(reverses), "fixture has no reverse-pair turn to relabel"
    bad = pack.turn_maneuver.copy()
    bad[reverses[0]] = Maneuver.STRAIGHT
    broken = dataclasses.replace(pack, turn_maneuver=bad)
    with pytest.raises(AssertionError, match="reverse"):
        build_pack_statics(broken, CFG)
