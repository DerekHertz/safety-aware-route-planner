"""Bitwise golden digests for the per-query cost precompute.

Phase 1(3a) in `docs/agents/handoff.md` hoists the pack-static half of
`compute_costs` to load time, computes the A* heuristic over nodes rather than
edge heads, and lifts the `edge_time_s` gather out of `arc_cost`. ADR-0009's
2026-09-18 amendment justifies that work on the grounds that it is *bitwise*
neutral -- not merely close. Nothing in the suite pinned that before this file:
the parity tests prove pyref and `sr_core` agree with **each other**, so a
refactor that shifted both engines' shared numpy inputs by an ulp would keep
every parity test green while silently moving routes.

This file is the missing half. It pins the precompute's output bits against
constants recorded before the hoist, so "bitwise identical" becomes a claim the
suite checks rather than one a commit message asserts.

What is pinned, and what deliberately is not
--------------------------------------------
`compute_costs` and `arc_cost` use only +, -, *, /, `clip`, `maximum` and
comparisons. Those are IEEE-754 exact and reproducible across platforms and
numpy builds, so a digest of their output is a legitimate constant to commit.
`sim.profiles` interpolates linearly with no transcendentals either, so
`at_time` snapshots are equally safe to pin.

`heuristic` is **not** pinned. It reaches `sin`/`cos`/`arcsin` through
`haversine_m`, and numpy may dispatch different SIMD kernels for those by build
and CPU, differing in the last ulp. A committed digest would make this file a
flaky probe of the machine rather than a guard on the code. The property 3a
actually needs from it is tested directly instead -- see
`test_heuristic_equals_a_per_node_gather`, which asserts the hoist's own
identity without caring what the absolute values are.

Regenerating
------------
Only after a *deliberate* cost-model change, and never to turn a red bar green:

    python -m tests.test_costs_golden        # prints a fresh GOLDEN literal
"""
from __future__ import annotations

import datetime
import hashlib
import os

import numpy as np
import pytest

from pyref.config import Config
from pyref.costs import arc_cost, compute_costs, heuristic
from pyref.geo import haversine_m
from pyref.graph import (
    Control,
    ControlConfidence,
    GraphPack,
    Maneuver,
    RoadClass,
)
from sim.snapshot import at_time, free_flow
from tests.helpers.fixtures import (
    cross_with_control,
    grid3x3,
    line3,
    stop_sign_left_city,
    unprotected_left_city,
)
from tests.helpers.toy_graphs import GraphBuilder

CFG = Config.load()

# Matches tests/test_parity_cpp.py, so the two files talk about the same sweep.
LAMBDAS = [0.0, 0.5, 1.5]

# A peak hour and a dead-of-night hour: the volume term is the only way the
# departure time reaches the cost model, and 3a's hoist is precisely a claim
# about which half of the model the departure time touches. Pinning only
# free-flow would leave that claim untested.
PEAK = datetime.datetime(2026, 7, 22, 17, 30)
NIGHT = datetime.datetime(2026, 7, 22, 3, 0)


def _median_cross(control: Control) -> GraphPack:
    """A junction of narrow, slow, divided roads -- the only corner of the
    model where `raw` goes NEGATIVE, because the median term (-w_med) outweighs
    a RIGHT's small severity plus the speed/lanes/volume terms.

    Both controls matter to 3a for different reasons. Under `Control.NONE` the
    negative score is observable, exercising the `maximum(raw, 0)` clamp and
    the sign of the hoisted `- w_med*median` term. Under a protected control
    the *pre-override* score is negative and then forced to zero -- and the
    spec says exactly `+0.0`. Folding that assignment into a multiplier (a
    tempting hoist: `raw * 0.0`) would silently yield `-0.0` here, which is a
    different bit pattern. See `test_protected_turns_are_positive_zero`.
    """
    b = GraphBuilder()
    c = b.node(0.0, 0.0, control=control)
    n = b.node(0.004, 0.0)
    s = b.node(-0.004, 0.0)
    e = b.node(0.0, 0.004)
    w = b.node(0.0, -0.004)
    for tail, head in ((s, c), (c, n), (w, c), (c, e)):
        b.edge(tail, head, length_m=500.0, median=True,
               road_class=RoadClass.service, speed_kph=20.0, lanes=1.0)
    return b.build()


def _packs():
    """The corpus. Breadth over depth: every control type reaches the override
    ladder in `compute_costs`, which is where a mis-folded hoist would do its
    damage. `test_corpus_is_not_vacuous` keeps this list honest."""
    yield "line3", line3()[0]
    yield "grid3x3", grid3x3()[0]
    yield "unprotected_left_city", unprotected_left_city()[0]
    yield "stop_sign_left_city", stop_sign_left_city()[0]
    yield "cross_untagged", cross_with_control(None)[0]
    for ctrl in Control:
        yield f"cross_{ctrl.name}", cross_with_control(ctrl)[0]
    # "Busy but not major" separates the edge_busy and edge_major branches,
    # which the unsafe-crossing predicate treats differently.
    yield "cross_busy_not_major", cross_with_control(
        Control.NONE, lanes=1, road_class=RoadClass.residential)[0]
    yield "median_cross_none", _median_cross(Control.NONE)
    yield "median_cross_protected", _median_cross(Control.SIGNAL_PROTECTED)


def _snapshots(pack):
    yield "free_flow", free_flow(pack, CFG)
    yield "peak", at_time(pack, CFG, PEAK)
    yield "night", at_time(pack, CFG, NIGHT)


def _digest(arr) -> str:
    """Hash dtype and shape alongside the bytes, so a silent dtype or layout
    change cannot slip through as an unchanged digest."""
    a = np.ascontiguousarray(arr)
    h = hashlib.sha256()
    h.update(str(a.dtype).encode())
    h.update(str(a.shape).encode())
    h.update(a.tobytes())
    return h.hexdigest()[:16]


def _digests_for(pack, snap) -> dict[str, str]:
    qc = compute_costs(pack, snap, CFG)
    out = {
        "edge_time_s": _digest(qc.edge_time_s),
        "turn_penalty_s": _digest(qc.turn_penalty_s),
        "turn_raw": _digest(qc.turn_raw),
        "edge_busy": _digest(qc.edge_busy),
        "edge_major": _digest(qc.edge_major),
        "turn_unsafe_type": _digest(qc.turn_unsafe_type),
        "turn_tier": _digest(qc.turn_tier),
        "v_max_mps": _digest(np.float64(qc.v_max_mps)),
    }
    for lam in LAMBDAS:
        out[f"arc_cost@{lam}"] = _digest(arc_cost(pack, qc, lam))
    return out


def _all_digests() -> dict[str, str]:
    flat: dict[str, str] = {}
    for name, pack in _packs():
        for label, snap in _snapshots(pack):
            for field, dig in _digests_for(pack, snap).items():
                flat[f"{name}|{label}|{field}"] = dig
    return flat


# --- recorded on the pre-3a tree, numpy 2.5.1 ----------------------------
GOLDEN: dict[str, str] = {
    "line3|free_flow|edge_time_s": "cfe14156e82bfece",
    "line3|free_flow|turn_penalty_s": "f2e9a364188f733f",
    "line3|free_flow|turn_raw": "4610a9d4fc0b280a",
    "line3|free_flow|edge_busy": "868cf335f7d0bdc2",
    "line3|free_flow|edge_major": "868cf335f7d0bdc2",
    "line3|free_flow|turn_unsafe_type": "124324c4b48dbd9b",
    "line3|free_flow|turn_tier": "2544951e74f61c27",
    "line3|free_flow|v_max_mps": "7c642df378cae3a2",
    "line3|free_flow|arc_cost@0.0": "2d2fbc508d706d74",
    "line3|free_flow|arc_cost@0.5": "7d9ace5b205b2473",
    "line3|free_flow|arc_cost@1.5": "44a8737acfe9a461",
    "line3|peak|edge_time_s": "6e1dc8f0192f0b0c",
    "line3|peak|turn_penalty_s": "f1ff181849d3f537",
    "line3|peak|turn_raw": "387ce57c5b03d375",
    "line3|peak|edge_busy": "868cf335f7d0bdc2",
    "line3|peak|edge_major": "868cf335f7d0bdc2",
    "line3|peak|turn_unsafe_type": "124324c4b48dbd9b",
    "line3|peak|turn_tier": "2544951e74f61c27",
    "line3|peak|v_max_mps": "7c642df378cae3a2",
    "line3|peak|arc_cost@0.0": "045d9f992bc10f7d",
    "line3|peak|arc_cost@0.5": "8fa14887d9007111",
    "line3|peak|arc_cost@1.5": "9f3cef3ac3559797",
    "line3|night|edge_time_s": "cfe14156e82bfece",
    "line3|night|turn_penalty_s": "6c6a5c5f43aa4220",
    "line3|night|turn_raw": "a98f0dc83d07dc12",
    "line3|night|edge_busy": "868cf335f7d0bdc2",
    "line3|night|edge_major": "868cf335f7d0bdc2",
    "line3|night|turn_unsafe_type": "124324c4b48dbd9b",
    "line3|night|turn_tier": "2544951e74f61c27",
    "line3|night|v_max_mps": "7c642df378cae3a2",
    "line3|night|arc_cost@0.0": "2d2fbc508d706d74",
    "line3|night|arc_cost@0.5": "32cb39e7e7c4b9c4",
    "line3|night|arc_cost@1.5": "039fae287433fd2b",
    "grid3x3|free_flow|edge_time_s": "a248ed2a259e2744",
    "grid3x3|free_flow|turn_penalty_s": "13d021bda1d5428d",
    "grid3x3|free_flow|turn_raw": "721f0eed7db813bf",
    "grid3x3|free_flow|edge_busy": "2810db3b67bae470",
    "grid3x3|free_flow|edge_major": "2810db3b67bae470",
    "grid3x3|free_flow|turn_unsafe_type": "54a60c39bf2dbf7b",
    "grid3x3|free_flow|turn_tier": "872e0056f2af2236",
    "grid3x3|free_flow|v_max_mps": "7c642df378cae3a2",
    "grid3x3|free_flow|arc_cost@0.0": "bbe15e1d44e6838d",
    "grid3x3|free_flow|arc_cost@0.5": "6af15145fed60d63",
    "grid3x3|free_flow|arc_cost@1.5": "58c4c4d77f53c74b",
    "grid3x3|peak|edge_time_s": "608210f6b60eb250",
    "grid3x3|peak|turn_penalty_s": "1c122f230e3d5278",
    "grid3x3|peak|turn_raw": "3dd282084871719f",
    "grid3x3|peak|edge_busy": "2810db3b67bae470",
    "grid3x3|peak|edge_major": "2810db3b67bae470",
    "grid3x3|peak|turn_unsafe_type": "54a60c39bf2dbf7b",
    "grid3x3|peak|turn_tier": "872e0056f2af2236",
    "grid3x3|peak|v_max_mps": "7c642df378cae3a2",
    "grid3x3|peak|arc_cost@0.0": "cab9ea2cab457317",
    "grid3x3|peak|arc_cost@0.5": "5e59b62cba12b4f6",
    "grid3x3|peak|arc_cost@1.5": "289adc660d6cc64c",
    "grid3x3|night|edge_time_s": "a248ed2a259e2744",
    "grid3x3|night|turn_penalty_s": "de095a5506395073",
    "grid3x3|night|turn_raw": "0c82450125129d8e",
    "grid3x3|night|edge_busy": "2810db3b67bae470",
    "grid3x3|night|edge_major": "2810db3b67bae470",
    "grid3x3|night|turn_unsafe_type": "54a60c39bf2dbf7b",
    "grid3x3|night|turn_tier": "872e0056f2af2236",
    "grid3x3|night|v_max_mps": "7c642df378cae3a2",
    "grid3x3|night|arc_cost@0.0": "bbe15e1d44e6838d",
    "grid3x3|night|arc_cost@0.5": "30b5eb5b0928d624",
    "grid3x3|night|arc_cost@1.5": "5ba13daaca71091d",
    "unprotected_left_city|free_flow|edge_time_s": "bb768866c44279c3",
    "unprotected_left_city|free_flow|turn_penalty_s": "630dcdf42272a4e7",
    "unprotected_left_city|free_flow|turn_raw": "104d18bcf4132cbd",
    "unprotected_left_city|free_flow|edge_busy": "920f8bc18e282c9d",
    "unprotected_left_city|free_flow|edge_major": "920f8bc18e282c9d",
    "unprotected_left_city|free_flow|turn_unsafe_type": "04bd2f92ec8b71bc",
    "unprotected_left_city|free_flow|turn_tier": "844ea00c3947bfd2",
    "unprotected_left_city|free_flow|v_max_mps": "21a175b00f49e49c",
    "unprotected_left_city|free_flow|arc_cost@0.0": "182030df94ead36c",
    "unprotected_left_city|free_flow|arc_cost@0.5": "08fc5fafc87b9ada",
    "unprotected_left_city|free_flow|arc_cost@1.5": "675a8a922e0ea6bc",
    "unprotected_left_city|peak|edge_time_s": "5456fe7a84016f64",
    "unprotected_left_city|peak|turn_penalty_s": "5d441c305eda2b0c",
    "unprotected_left_city|peak|turn_raw": "c95f5f1b3beee82b",
    "unprotected_left_city|peak|edge_busy": "920f8bc18e282c9d",
    "unprotected_left_city|peak|edge_major": "920f8bc18e282c9d",
    "unprotected_left_city|peak|turn_unsafe_type": "04bd2f92ec8b71bc",
    "unprotected_left_city|peak|turn_tier": "8a119d24d1c36306",
    "unprotected_left_city|peak|v_max_mps": "21a175b00f49e49c",
    "unprotected_left_city|peak|arc_cost@0.0": "055b422bd4173f23",
    "unprotected_left_city|peak|arc_cost@0.5": "a2612969ff5a43b3",
    "unprotected_left_city|peak|arc_cost@1.5": "46d40afabf47804a",
    "unprotected_left_city|night|edge_time_s": "bb768866c44279c3",
    "unprotected_left_city|night|turn_penalty_s": "6900706e86961854",
    "unprotected_left_city|night|turn_raw": "3b0ac00c09687cc8",
    "unprotected_left_city|night|edge_busy": "920f8bc18e282c9d",
    "unprotected_left_city|night|edge_major": "920f8bc18e282c9d",
    "unprotected_left_city|night|turn_unsafe_type": "04bd2f92ec8b71bc",
    "unprotected_left_city|night|turn_tier": "4da3b1ddafc07b4d",
    "unprotected_left_city|night|v_max_mps": "21a175b00f49e49c",
    "unprotected_left_city|night|arc_cost@0.0": "182030df94ead36c",
    "unprotected_left_city|night|arc_cost@0.5": "9bc1c1ccde48e292",
    "unprotected_left_city|night|arc_cost@1.5": "34c21683cf0f332c",
    "stop_sign_left_city|free_flow|edge_time_s": "bb768866c44279c3",
    "stop_sign_left_city|free_flow|turn_penalty_s": "d4097d43fbfae630",
    "stop_sign_left_city|free_flow|turn_raw": "2bef5a5bbf0a7a82",
    "stop_sign_left_city|free_flow|edge_busy": "920f8bc18e282c9d",
    "stop_sign_left_city|free_flow|edge_major": "920f8bc18e282c9d",
    "stop_sign_left_city|free_flow|turn_unsafe_type": "04bd2f92ec8b71bc",
    "stop_sign_left_city|free_flow|turn_tier": "0b8a96ed92876138",
    "stop_sign_left_city|free_flow|v_max_mps": "21a175b00f49e49c",
    "stop_sign_left_city|free_flow|arc_cost@0.0": "182030df94ead36c",
    "stop_sign_left_city|free_flow|arc_cost@0.5": "cc8be920902fd779",
    "stop_sign_left_city|free_flow|arc_cost@1.5": "863072268d2c6b8f",
    "stop_sign_left_city|peak|edge_time_s": "5456fe7a84016f64",
    "stop_sign_left_city|peak|turn_penalty_s": "11db42cecc02933c",
    "stop_sign_left_city|peak|turn_raw": "1e52d9a6f26dd263",
    "stop_sign_left_city|peak|edge_busy": "920f8bc18e282c9d",
    "stop_sign_left_city|peak|edge_major": "920f8bc18e282c9d",
    "stop_sign_left_city|peak|turn_unsafe_type": "04bd2f92ec8b71bc",
    "stop_sign_left_city|peak|turn_tier": "0b8a96ed92876138",
    "stop_sign_left_city|peak|v_max_mps": "21a175b00f49e49c",
    "stop_sign_left_city|peak|arc_cost@0.0": "055b422bd4173f23",
    "stop_sign_left_city|peak|arc_cost@0.5": "3019ffd056dfde6f",
    "stop_sign_left_city|peak|arc_cost@1.5": "86ba121fa061c9fe",
    "stop_sign_left_city|night|edge_time_s": "bb768866c44279c3",
    "stop_sign_left_city|night|turn_penalty_s": "a49ac627f8a81113",
    "stop_sign_left_city|night|turn_raw": "306f3a3a756212e2",
    "stop_sign_left_city|night|edge_busy": "920f8bc18e282c9d",
    "stop_sign_left_city|night|edge_major": "920f8bc18e282c9d",
    "stop_sign_left_city|night|turn_unsafe_type": "04bd2f92ec8b71bc",
    "stop_sign_left_city|night|turn_tier": "afdafc296577097a",
    "stop_sign_left_city|night|v_max_mps": "21a175b00f49e49c",
    "stop_sign_left_city|night|arc_cost@0.0": "182030df94ead36c",
    "stop_sign_left_city|night|arc_cost@0.5": "ae93c3b8795c7bc5",
    "stop_sign_left_city|night|arc_cost@1.5": "a69af61942e4889c",
    "cross_untagged|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_untagged|free_flow|turn_penalty_s": "869300fb018c5bdd",
    "cross_untagged|free_flow|turn_raw": "b8fe97dce489cfd7",
    "cross_untagged|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_untagged|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_untagged|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_untagged|free_flow|turn_tier": "f0a405349589051b",
    "cross_untagged|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_untagged|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_untagged|free_flow|arc_cost@0.5": "e0f5e9cae4ac01cc",
    "cross_untagged|free_flow|arc_cost@1.5": "6a1bb9cdb83ed99d",
    "cross_untagged|peak|edge_time_s": "7389c83389b79dcc",
    "cross_untagged|peak|turn_penalty_s": "af50d93f1c957b78",
    "cross_untagged|peak|turn_raw": "1a3a2b3a6424afd9",
    "cross_untagged|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_untagged|peak|edge_major": "c9b0e66cf9597f67",
    "cross_untagged|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_untagged|peak|turn_tier": "f0a405349589051b",
    "cross_untagged|peak|v_max_mps": "21a175b00f49e49c",
    "cross_untagged|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_untagged|peak|arc_cost@0.5": "3cc03652193fb430",
    "cross_untagged|peak|arc_cost@1.5": "cb30bd92af8ff41a",
    "cross_untagged|night|edge_time_s": "d03f72830bbbd10d",
    "cross_untagged|night|turn_penalty_s": "e3548dea54c44da0",
    "cross_untagged|night|turn_raw": "33bdd9db1deb3586",
    "cross_untagged|night|edge_busy": "c9b0e66cf9597f67",
    "cross_untagged|night|edge_major": "c9b0e66cf9597f67",
    "cross_untagged|night|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_untagged|night|turn_tier": "80d9a1bcc4053309",
    "cross_untagged|night|v_max_mps": "21a175b00f49e49c",
    "cross_untagged|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_untagged|night|arc_cost@0.5": "8145f0a0ce2b35de",
    "cross_untagged|night|arc_cost@1.5": "9b645eceb894392c",
    "cross_NONE|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_NONE|free_flow|turn_penalty_s": "8ec462698efc1f18",
    "cross_NONE|free_flow|turn_raw": "4c3f821a2c1b436e",
    "cross_NONE|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_NONE|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_NONE|free_flow|turn_unsafe_type": "33bf3e7250259649",
    "cross_NONE|free_flow|turn_tier": "c7606818fafd37dc",
    "cross_NONE|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_NONE|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_NONE|free_flow|arc_cost@0.5": "f8574712609597fb",
    "cross_NONE|free_flow|arc_cost@1.5": "84790e9d631942de",
    "cross_NONE|peak|edge_time_s": "7389c83389b79dcc",
    "cross_NONE|peak|turn_penalty_s": "208587efb0b0adba",
    "cross_NONE|peak|turn_raw": "8a8a484212fdb042",
    "cross_NONE|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_NONE|peak|edge_major": "c9b0e66cf9597f67",
    "cross_NONE|peak|turn_unsafe_type": "33bf3e7250259649",
    "cross_NONE|peak|turn_tier": "555286be0be4469a",
    "cross_NONE|peak|v_max_mps": "21a175b00f49e49c",
    "cross_NONE|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_NONE|peak|arc_cost@0.5": "bb5d66610bcdd7ec",
    "cross_NONE|peak|arc_cost@1.5": "1b690938129c39fe",
    "cross_NONE|night|edge_time_s": "d03f72830bbbd10d",
    "cross_NONE|night|turn_penalty_s": "211af8a389e0e06d",
    "cross_NONE|night|turn_raw": "42994ab9b77f9d0a",
    "cross_NONE|night|edge_busy": "c9b0e66cf9597f67",
    "cross_NONE|night|edge_major": "c9b0e66cf9597f67",
    "cross_NONE|night|turn_unsafe_type": "33bf3e7250259649",
    "cross_NONE|night|turn_tier": "22332a8366a1c8ff",
    "cross_NONE|night|v_max_mps": "21a175b00f49e49c",
    "cross_NONE|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_NONE|night|arc_cost@0.5": "49d9000e81255c7c",
    "cross_NONE|night|arc_cost@1.5": "e40625f829c89430",
    "cross_STOP_2WAY|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_STOP_2WAY|free_flow|turn_penalty_s": "31717a94b225e8c4",
    "cross_STOP_2WAY|free_flow|turn_raw": "44b006c4edc4ada9",
    "cross_STOP_2WAY|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_STOP_2WAY|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_STOP_2WAY|free_flow|turn_unsafe_type": "33bf3e7250259649",
    "cross_STOP_2WAY|free_flow|turn_tier": "44879d9db1841c91",
    "cross_STOP_2WAY|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_STOP_2WAY|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_STOP_2WAY|free_flow|arc_cost@0.5": "37382661591ed688",
    "cross_STOP_2WAY|free_flow|arc_cost@1.5": "7163099455d584f9",
    "cross_STOP_2WAY|peak|edge_time_s": "7389c83389b79dcc",
    "cross_STOP_2WAY|peak|turn_penalty_s": "d9a40457484d8edf",
    "cross_STOP_2WAY|peak|turn_raw": "e7a34198168edd82",
    "cross_STOP_2WAY|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_STOP_2WAY|peak|edge_major": "c9b0e66cf9597f67",
    "cross_STOP_2WAY|peak|turn_unsafe_type": "33bf3e7250259649",
    "cross_STOP_2WAY|peak|turn_tier": "44879d9db1841c91",
    "cross_STOP_2WAY|peak|v_max_mps": "21a175b00f49e49c",
    "cross_STOP_2WAY|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_STOP_2WAY|peak|arc_cost@0.5": "dd6c66d403e15952",
    "cross_STOP_2WAY|peak|arc_cost@1.5": "c5a60f1c863fc0b4",
    "cross_STOP_2WAY|night|edge_time_s": "d03f72830bbbd10d",
    "cross_STOP_2WAY|night|turn_penalty_s": "44ee92fbe29fe6ef",
    "cross_STOP_2WAY|night|turn_raw": "b6a3738de2b4803f",
    "cross_STOP_2WAY|night|edge_busy": "c9b0e66cf9597f67",
    "cross_STOP_2WAY|night|edge_major": "c9b0e66cf9597f67",
    "cross_STOP_2WAY|night|turn_unsafe_type": "33bf3e7250259649",
    "cross_STOP_2WAY|night|turn_tier": "0bb3957eb66e21b9",
    "cross_STOP_2WAY|night|v_max_mps": "21a175b00f49e49c",
    "cross_STOP_2WAY|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_STOP_2WAY|night|arc_cost@0.5": "e644c531af51152c",
    "cross_STOP_2WAY|night|arc_cost@1.5": "4fa57a86b564392d",
    "cross_STOP_4WAY|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_STOP_4WAY|free_flow|turn_penalty_s": "820b86b8a515818b",
    "cross_STOP_4WAY|free_flow|turn_raw": "98c8278942667cda",
    "cross_STOP_4WAY|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_STOP_4WAY|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_STOP_4WAY|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_STOP_4WAY|free_flow|turn_tier": "e14427195e2f23de",
    "cross_STOP_4WAY|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_STOP_4WAY|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_STOP_4WAY|free_flow|arc_cost@0.5": "fce8b5a4cc1357dd",
    "cross_STOP_4WAY|free_flow|arc_cost@1.5": "176608e8c174d1b9",
    "cross_STOP_4WAY|peak|edge_time_s": "7389c83389b79dcc",
    "cross_STOP_4WAY|peak|turn_penalty_s": "e15d0161b44d3b0e",
    "cross_STOP_4WAY|peak|turn_raw": "099cb416c421117f",
    "cross_STOP_4WAY|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_STOP_4WAY|peak|edge_major": "c9b0e66cf9597f67",
    "cross_STOP_4WAY|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_STOP_4WAY|peak|turn_tier": "e14427195e2f23de",
    "cross_STOP_4WAY|peak|v_max_mps": "21a175b00f49e49c",
    "cross_STOP_4WAY|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_STOP_4WAY|peak|arc_cost@0.5": "ab9d75cd78a72012",
    "cross_STOP_4WAY|peak|arc_cost@1.5": "52a070474856ebaf",
    "cross_STOP_4WAY|night|edge_time_s": "d03f72830bbbd10d",
    "cross_STOP_4WAY|night|turn_penalty_s": "a45f6d225a54a6f6",
    "cross_STOP_4WAY|night|turn_raw": "524f73928204e964",
    "cross_STOP_4WAY|night|edge_busy": "c9b0e66cf9597f67",
    "cross_STOP_4WAY|night|edge_major": "c9b0e66cf9597f67",
    "cross_STOP_4WAY|night|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_STOP_4WAY|night|turn_tier": "971d5168b7b9c739",
    "cross_STOP_4WAY|night|v_max_mps": "21a175b00f49e49c",
    "cross_STOP_4WAY|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_STOP_4WAY|night|arc_cost@0.5": "7073987a6f3311ac",
    "cross_STOP_4WAY|night|arc_cost@1.5": "c024265f94637ddf",
    "cross_SIGNAL_PERMISSIVE|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_SIGNAL_PERMISSIVE|free_flow|turn_penalty_s": "fe50e202ff4f2d04",
    "cross_SIGNAL_PERMISSIVE|free_flow|turn_raw": "548d05dc67ebec91",
    "cross_SIGNAL_PERMISSIVE|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_SIGNAL_PERMISSIVE|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_SIGNAL_PERMISSIVE|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_SIGNAL_PERMISSIVE|free_flow|turn_tier": "d312f7532c823970",
    "cross_SIGNAL_PERMISSIVE|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_SIGNAL_PERMISSIVE|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_SIGNAL_PERMISSIVE|free_flow|arc_cost@0.5": "3dfa3c0fd8dda915",
    "cross_SIGNAL_PERMISSIVE|free_flow|arc_cost@1.5": "8e52eb559525800a",
    "cross_SIGNAL_PERMISSIVE|peak|edge_time_s": "7389c83389b79dcc",
    "cross_SIGNAL_PERMISSIVE|peak|turn_penalty_s": "d056846ee1f51322",
    "cross_SIGNAL_PERMISSIVE|peak|turn_raw": "6258608030bae486",
    "cross_SIGNAL_PERMISSIVE|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_SIGNAL_PERMISSIVE|peak|edge_major": "c9b0e66cf9597f67",
    "cross_SIGNAL_PERMISSIVE|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_SIGNAL_PERMISSIVE|peak|turn_tier": "d312f7532c823970",
    "cross_SIGNAL_PERMISSIVE|peak|v_max_mps": "21a175b00f49e49c",
    "cross_SIGNAL_PERMISSIVE|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_SIGNAL_PERMISSIVE|peak|arc_cost@0.5": "6f560c053452a549",
    "cross_SIGNAL_PERMISSIVE|peak|arc_cost@1.5": "fcd66d8eb17eea53",
    "cross_SIGNAL_PERMISSIVE|night|edge_time_s": "d03f72830bbbd10d",
    "cross_SIGNAL_PERMISSIVE|night|turn_penalty_s": "846f44534fa61688",
    "cross_SIGNAL_PERMISSIVE|night|turn_raw": "2f4b40b535c50d3a",
    "cross_SIGNAL_PERMISSIVE|night|edge_busy": "c9b0e66cf9597f67",
    "cross_SIGNAL_PERMISSIVE|night|edge_major": "c9b0e66cf9597f67",
    "cross_SIGNAL_PERMISSIVE|night|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_SIGNAL_PERMISSIVE|night|turn_tier": "d312f7532c823970",
    "cross_SIGNAL_PERMISSIVE|night|v_max_mps": "21a175b00f49e49c",
    "cross_SIGNAL_PERMISSIVE|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_SIGNAL_PERMISSIVE|night|arc_cost@0.5": "7a4ad7f9c0fcb663",
    "cross_SIGNAL_PERMISSIVE|night|arc_cost@1.5": "344fd8e807444fdd",
    "cross_SIGNAL_PROTECTED|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_SIGNAL_PROTECTED|free_flow|turn_penalty_s": "ac6a606597a07937",
    "cross_SIGNAL_PROTECTED|free_flow|turn_raw": "9ff68d14bb135528",
    "cross_SIGNAL_PROTECTED|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_SIGNAL_PROTECTED|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_SIGNAL_PROTECTED|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_SIGNAL_PROTECTED|free_flow|turn_tier": "971d5168b7b9c739",
    "cross_SIGNAL_PROTECTED|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_SIGNAL_PROTECTED|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_SIGNAL_PROTECTED|free_flow|arc_cost@0.5": "10529304d2c19304",
    "cross_SIGNAL_PROTECTED|free_flow|arc_cost@1.5": "2fe12a97683fa409",
    "cross_SIGNAL_PROTECTED|peak|edge_time_s": "7389c83389b79dcc",
    "cross_SIGNAL_PROTECTED|peak|turn_penalty_s": "f88112eba6bc8aca",
    "cross_SIGNAL_PROTECTED|peak|turn_raw": "6d266031e03c3ec8",
    "cross_SIGNAL_PROTECTED|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_SIGNAL_PROTECTED|peak|edge_major": "c9b0e66cf9597f67",
    "cross_SIGNAL_PROTECTED|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_SIGNAL_PROTECTED|peak|turn_tier": "971d5168b7b9c739",
    "cross_SIGNAL_PROTECTED|peak|v_max_mps": "21a175b00f49e49c",
    "cross_SIGNAL_PROTECTED|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_SIGNAL_PROTECTED|peak|arc_cost@0.5": "f90e4e685b616099",
    "cross_SIGNAL_PROTECTED|peak|arc_cost@1.5": "832a8360b34a1cbd",
    "cross_SIGNAL_PROTECTED|night|edge_time_s": "d03f72830bbbd10d",
    "cross_SIGNAL_PROTECTED|night|turn_penalty_s": "6ab11ec59477155d",
    "cross_SIGNAL_PROTECTED|night|turn_raw": "e7bc0e9cffbbaa11",
    "cross_SIGNAL_PROTECTED|night|edge_busy": "c9b0e66cf9597f67",
    "cross_SIGNAL_PROTECTED|night|edge_major": "c9b0e66cf9597f67",
    "cross_SIGNAL_PROTECTED|night|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_SIGNAL_PROTECTED|night|turn_tier": "971d5168b7b9c739",
    "cross_SIGNAL_PROTECTED|night|v_max_mps": "21a175b00f49e49c",
    "cross_SIGNAL_PROTECTED|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_SIGNAL_PROTECTED|night|arc_cost@0.5": "76fd3728c7e416c1",
    "cross_SIGNAL_PROTECTED|night|arc_cost@1.5": "360cbe50a08e9077",
    "cross_ROUNDABOUT|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_ROUNDABOUT|free_flow|turn_penalty_s": "ac6a606597a07937",
    "cross_ROUNDABOUT|free_flow|turn_raw": "9ff68d14bb135528",
    "cross_ROUNDABOUT|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_ROUNDABOUT|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_ROUNDABOUT|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_ROUNDABOUT|free_flow|turn_tier": "971d5168b7b9c739",
    "cross_ROUNDABOUT|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_ROUNDABOUT|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_ROUNDABOUT|free_flow|arc_cost@0.5": "10529304d2c19304",
    "cross_ROUNDABOUT|free_flow|arc_cost@1.5": "2fe12a97683fa409",
    "cross_ROUNDABOUT|peak|edge_time_s": "7389c83389b79dcc",
    "cross_ROUNDABOUT|peak|turn_penalty_s": "f88112eba6bc8aca",
    "cross_ROUNDABOUT|peak|turn_raw": "6d266031e03c3ec8",
    "cross_ROUNDABOUT|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_ROUNDABOUT|peak|edge_major": "c9b0e66cf9597f67",
    "cross_ROUNDABOUT|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_ROUNDABOUT|peak|turn_tier": "971d5168b7b9c739",
    "cross_ROUNDABOUT|peak|v_max_mps": "21a175b00f49e49c",
    "cross_ROUNDABOUT|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_ROUNDABOUT|peak|arc_cost@0.5": "f90e4e685b616099",
    "cross_ROUNDABOUT|peak|arc_cost@1.5": "832a8360b34a1cbd",
    "cross_ROUNDABOUT|night|edge_time_s": "d03f72830bbbd10d",
    "cross_ROUNDABOUT|night|turn_penalty_s": "6ab11ec59477155d",
    "cross_ROUNDABOUT|night|turn_raw": "e7bc0e9cffbbaa11",
    "cross_ROUNDABOUT|night|edge_busy": "c9b0e66cf9597f67",
    "cross_ROUNDABOUT|night|edge_major": "c9b0e66cf9597f67",
    "cross_ROUNDABOUT|night|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_ROUNDABOUT|night|turn_tier": "971d5168b7b9c739",
    "cross_ROUNDABOUT|night|v_max_mps": "21a175b00f49e49c",
    "cross_ROUNDABOUT|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_ROUNDABOUT|night|arc_cost@0.5": "76fd3728c7e416c1",
    "cross_ROUNDABOUT|night|arc_cost@1.5": "360cbe50a08e9077",
    "cross_YIELD|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_YIELD|free_flow|turn_penalty_s": "8b37fb22a294a1ac",
    "cross_YIELD|free_flow|turn_raw": "0fb6b7b3668a313b",
    "cross_YIELD|free_flow|edge_busy": "c9b0e66cf9597f67",
    "cross_YIELD|free_flow|edge_major": "c9b0e66cf9597f67",
    "cross_YIELD|free_flow|turn_unsafe_type": "1b7ee18c4d607a41",
    "cross_YIELD|free_flow|turn_tier": "6a20c0ef789fd06d",
    "cross_YIELD|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_YIELD|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_YIELD|free_flow|arc_cost@0.5": "d3f32a7893203fe7",
    "cross_YIELD|free_flow|arc_cost@1.5": "6c13a06097d0c1a9",
    "cross_YIELD|peak|edge_time_s": "7389c83389b79dcc",
    "cross_YIELD|peak|turn_penalty_s": "85d3cac6e1df3e40",
    "cross_YIELD|peak|turn_raw": "45aedd5c0683326a",
    "cross_YIELD|peak|edge_busy": "c9b0e66cf9597f67",
    "cross_YIELD|peak|edge_major": "c9b0e66cf9597f67",
    "cross_YIELD|peak|turn_unsafe_type": "1b7ee18c4d607a41",
    "cross_YIELD|peak|turn_tier": "6a20c0ef789fd06d",
    "cross_YIELD|peak|v_max_mps": "21a175b00f49e49c",
    "cross_YIELD|peak|arc_cost@0.0": "5d967f6dd22ca7ec",
    "cross_YIELD|peak|arc_cost@0.5": "a3b71951ee183bf7",
    "cross_YIELD|peak|arc_cost@1.5": "6f71c2ae8cada01f",
    "cross_YIELD|night|edge_time_s": "d03f72830bbbd10d",
    "cross_YIELD|night|turn_penalty_s": "46ac955256308570",
    "cross_YIELD|night|turn_raw": "9fdef4cd84fdff43",
    "cross_YIELD|night|edge_busy": "c9b0e66cf9597f67",
    "cross_YIELD|night|edge_major": "c9b0e66cf9597f67",
    "cross_YIELD|night|turn_unsafe_type": "1b7ee18c4d607a41",
    "cross_YIELD|night|turn_tier": "fe07c1b800bdfa3b",
    "cross_YIELD|night|v_max_mps": "21a175b00f49e49c",
    "cross_YIELD|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_YIELD|night|arc_cost@0.5": "eb8791f7eb5285c5",
    "cross_YIELD|night|arc_cost@1.5": "1051c396e3e5a5e8",
    "cross_busy_not_major|free_flow|edge_time_s": "d03f72830bbbd10d",
    "cross_busy_not_major|free_flow|turn_penalty_s": "de1c9c6b9be7e3a6",
    "cross_busy_not_major|free_flow|turn_raw": "7a99e6a9d23608ae",
    "cross_busy_not_major|free_flow|edge_busy": "f1f2dd25b61c5cd1",
    "cross_busy_not_major|free_flow|edge_major": "f1f2dd25b61c5cd1",
    "cross_busy_not_major|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_busy_not_major|free_flow|turn_tier": "2e7f579ae86e6d9b",
    "cross_busy_not_major|free_flow|v_max_mps": "21a175b00f49e49c",
    "cross_busy_not_major|free_flow|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_busy_not_major|free_flow|arc_cost@0.5": "0a04b729adf3fc9c",
    "cross_busy_not_major|free_flow|arc_cost@1.5": "d92842393aa1651e",
    "cross_busy_not_major|peak|edge_time_s": "74876ead3206f59c",
    "cross_busy_not_major|peak|turn_penalty_s": "da9a680e9443c609",
    "cross_busy_not_major|peak|turn_raw": "2946123f99d4a08b",
    "cross_busy_not_major|peak|edge_busy": "f1f2dd25b61c5cd1",
    "cross_busy_not_major|peak|edge_major": "f1f2dd25b61c5cd1",
    "cross_busy_not_major|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_busy_not_major|peak|turn_tier": "22332a8366a1c8ff",
    "cross_busy_not_major|peak|v_max_mps": "21a175b00f49e49c",
    "cross_busy_not_major|peak|arc_cost@0.0": "845d9a9e2ce47d0c",
    "cross_busy_not_major|peak|arc_cost@0.5": "64cc834051d5f561",
    "cross_busy_not_major|peak|arc_cost@1.5": "332645aaa7afd5c4",
    "cross_busy_not_major|night|edge_time_s": "d03f72830bbbd10d",
    "cross_busy_not_major|night|turn_penalty_s": "de4fca560692d880",
    "cross_busy_not_major|night|turn_raw": "4fc50c6a947849f7",
    "cross_busy_not_major|night|edge_busy": "f1f2dd25b61c5cd1",
    "cross_busy_not_major|night|edge_major": "f1f2dd25b61c5cd1",
    "cross_busy_not_major|night|turn_unsafe_type": "dcd69938ad9a459c",
    "cross_busy_not_major|night|turn_tier": "22332a8366a1c8ff",
    "cross_busy_not_major|night|v_max_mps": "21a175b00f49e49c",
    "cross_busy_not_major|night|arc_cost@0.0": "da7a4be40e6f5d4f",
    "cross_busy_not_major|night|arc_cost@0.5": "85ddd224239de499",
    "cross_busy_not_major|night|arc_cost@1.5": "c3bfe41a0bf1fd74",
    "median_cross_none|free_flow|edge_time_s": "771df2586e18a0f2",
    "median_cross_none|free_flow|turn_penalty_s": "1d7bd33fdcca104e",
    "median_cross_none|free_flow|turn_raw": "be216e935d0d5380",
    "median_cross_none|free_flow|edge_busy": "f1f2dd25b61c5cd1",
    "median_cross_none|free_flow|edge_major": "f1f2dd25b61c5cd1",
    "median_cross_none|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "median_cross_none|free_flow|turn_tier": "305e33acfa6275f2",
    "median_cross_none|free_flow|v_max_mps": "c5ec1a77dc918809",
    "median_cross_none|free_flow|arc_cost@0.0": "64c8a5e2fd52c89a",
    "median_cross_none|free_flow|arc_cost@0.5": "6aa7d73c1ba80fbc",
    "median_cross_none|free_flow|arc_cost@1.5": "bccef79e42d389d4",
    "median_cross_none|peak|edge_time_s": "18385a6eb895d0a5",
    "median_cross_none|peak|turn_penalty_s": "1688704eb10eb944",
    "median_cross_none|peak|turn_raw": "da9bbb2f00b51e9c",
    "median_cross_none|peak|edge_busy": "f1f2dd25b61c5cd1",
    "median_cross_none|peak|edge_major": "f1f2dd25b61c5cd1",
    "median_cross_none|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "median_cross_none|peak|turn_tier": "305e33acfa6275f2",
    "median_cross_none|peak|v_max_mps": "c5ec1a77dc918809",
    "median_cross_none|peak|arc_cost@0.0": "2d21d6a87b76430b",
    "median_cross_none|peak|arc_cost@0.5": "4eccece9a0b08451",
    "median_cross_none|peak|arc_cost@1.5": "0d3ea40b968d2bbe",
    "median_cross_none|night|edge_time_s": "771df2586e18a0f2",
    "median_cross_none|night|turn_penalty_s": "96b587f1cc259c6e",
    "median_cross_none|night|turn_raw": "726e290003c0c718",
    "median_cross_none|night|edge_busy": "f1f2dd25b61c5cd1",
    "median_cross_none|night|edge_major": "f1f2dd25b61c5cd1",
    "median_cross_none|night|turn_unsafe_type": "dcd69938ad9a459c",
    "median_cross_none|night|turn_tier": "305e33acfa6275f2",
    "median_cross_none|night|v_max_mps": "c5ec1a77dc918809",
    "median_cross_none|night|arc_cost@0.0": "64c8a5e2fd52c89a",
    "median_cross_none|night|arc_cost@0.5": "0485e200a23756d2",
    "median_cross_none|night|arc_cost@1.5": "78b42f7d4249c563",
    "median_cross_protected|free_flow|edge_time_s": "771df2586e18a0f2",
    "median_cross_protected|free_flow|turn_penalty_s": "bccf3021bd2f0b5f",
    "median_cross_protected|free_flow|turn_raw": "aeb64c4eba31e980",
    "median_cross_protected|free_flow|edge_busy": "f1f2dd25b61c5cd1",
    "median_cross_protected|free_flow|edge_major": "f1f2dd25b61c5cd1",
    "median_cross_protected|free_flow|turn_unsafe_type": "dcd69938ad9a459c",
    "median_cross_protected|free_flow|turn_tier": "971d5168b7b9c739",
    "median_cross_protected|free_flow|v_max_mps": "c5ec1a77dc918809",
    "median_cross_protected|free_flow|arc_cost@0.0": "64c8a5e2fd52c89a",
    "median_cross_protected|free_flow|arc_cost@0.5": "dccb5e0db0f75285",
    "median_cross_protected|free_flow|arc_cost@1.5": "67947d3418a849d8",
    "median_cross_protected|peak|edge_time_s": "18385a6eb895d0a5",
    "median_cross_protected|peak|turn_penalty_s": "f9b135d67db7c593",
    "median_cross_protected|peak|turn_raw": "6d32c0c64e70ded7",
    "median_cross_protected|peak|edge_busy": "f1f2dd25b61c5cd1",
    "median_cross_protected|peak|edge_major": "f1f2dd25b61c5cd1",
    "median_cross_protected|peak|turn_unsafe_type": "dcd69938ad9a459c",
    "median_cross_protected|peak|turn_tier": "971d5168b7b9c739",
    "median_cross_protected|peak|v_max_mps": "c5ec1a77dc918809",
    "median_cross_protected|peak|arc_cost@0.0": "2d21d6a87b76430b",
    "median_cross_protected|peak|arc_cost@0.5": "3422c10bc27bc28b",
    "median_cross_protected|peak|arc_cost@1.5": "715964db693093fb",
    "median_cross_protected|night|edge_time_s": "771df2586e18a0f2",
    "median_cross_protected|night|turn_penalty_s": "82571a4f74e6346d",
    "median_cross_protected|night|turn_raw": "1cd752c05698366f",
    "median_cross_protected|night|edge_busy": "f1f2dd25b61c5cd1",
    "median_cross_protected|night|edge_major": "f1f2dd25b61c5cd1",
    "median_cross_protected|night|turn_unsafe_type": "dcd69938ad9a459c",
    "median_cross_protected|night|turn_tier": "971d5168b7b9c739",
    "median_cross_protected|night|v_max_mps": "c5ec1a77dc918809",
    "median_cross_protected|night|arc_cost@0.0": "64c8a5e2fd52c89a",
    "median_cross_protected|night|arc_cost@0.5": "ded2aa3af78053d5",
    "median_cross_protected|night|arc_cost@1.5": "241f44a5ebac9fb7",
}


def test_cost_arrays_are_bitwise_unchanged():
    got = _all_digests()
    assert set(got) == set(GOLDEN), (
        "the pinned corpus changed shape; regenerate deliberately "
        "(python -m tests.test_costs_golden)")
    bad = {k: (GOLDEN[k], got[k]) for k in sorted(GOLDEN) if GOLDEN[k] != got[k]}
    assert not bad, (
        f"{len(bad)} cost array(s) changed bitwise, e.g. "
        + ", ".join(f"{k}: {want} -> {now}"
                    for k, (want, now) in list(bad.items())[:5]))


def test_corpus_is_not_vacuous():
    """A golden hash over data that exercises three branches would sail through
    the 3a refactor while proving nothing. Assert the corpus actually reaches
    the interesting states -- via observable outputs, not a second copy of the
    cost model's masks, which would just drift."""
    controls, confidences, maneuvers = set(), set(), set()
    tiers, unsafe_types = set(), set()
    busy_seen = major_seen = zeroed_seen = negative_seen = False

    for _, pack in _packs():
        controls.update(np.unique(pack.edge_approach_control).tolist())
        confidences.update(np.unique(pack.edge_control_confidence).tolist())
        maneuvers.update(np.unique(pack.turn_maneuver).tolist())
        for _, snap in _snapshots(pack):
            qc = compute_costs(pack, snap, CFG)
            tiers.update(np.unique(qc.turn_tier).tolist())
            unsafe_types.update(np.unique(qc.turn_unsafe_type).tolist())
            busy_seen |= bool(qc.edge_busy.any())
            major_seen |= bool(qc.edge_major.any())
            # raw is forced to exactly 0.0 under a protected control and can go
            # negative via the median term -- the two cases where folding a
            # multiplier the wrong way yields -0.0 where +0.0 is expected.
            zeroed_seen |= bool((qc.turn_raw == 0.0).any())
            negative_seen |= bool((qc.turn_raw < 0.0).any())

    assert controls == {c.value for c in Control}, "corpus misses a control type"
    assert ControlConfidence.OBSERVED in confidences
    assert ControlConfidence.INFERRED in confidences
    assert maneuvers == {m.value for m in Maneuver}, "corpus misses a maneuver"
    assert tiers == {0, 1, 2}, "corpus never reaches every tier band"
    assert unsafe_types == {0, 1, 2}, "corpus never reaches every unsafe type"
    assert busy_seen and major_seen
    assert zeroed_seen and negative_seen


def test_protected_turns_are_positive_zero():
    """A protected control sets `raw` to exactly `+0.0`, never `-0.0`.

    Stated on its own rather than left to the digest because the distinction is
    invisible to `==` (`-0.0 == 0.0` is True) and survives most eyeballing, but
    changes the bytes -- so a hoist that introduced it would fail the golden
    digest with no clue as to why. `median_cross_protected` is the case that
    can actually produce it: its pre-override scores are negative."""
    for name, pack in _packs():
        for label, snap in _snapshots(pack):
            raw = compute_costs(pack, snap, CFG).turn_raw
            zeros = raw[raw == 0.0]
            assert not np.signbit(zeros).any(), (
                f"{name}/{label} produced -0.0 in turn_raw")


def _dest_points(pack):
    """A few destinations per pack, including exact node hits (h == 0)."""
    n = pack.num_nodes
    for i in (0, n // 2, n - 1):
        yield float(pack.node_lat[i]), float(pack.node_lon[i])


def _heuristic_by_node_gather(pack, qc, dest_lat: float, dest_lon: float):
    """What Phase 1(3a) will replace `heuristic`'s body with: haversine over
    the nodes, then gather to edge heads."""
    per_node = haversine_m(pack.node_lat, pack.node_lon, dest_lat, dest_lon)
    return (per_node / qc.v_max_mps)[pack.edge_head]


def test_heuristic_equals_a_per_node_gather():
    """The one property 3a needs from `heuristic`, stated as an identity so it
    holds on whatever numpy the machine happens to have.

    This is not obviously true: it applies the same ufuncs to an array of
    `num_nodes` elements instead of `num_edges`, and numpy may take a different
    SIMD path for a different length, which for sin/cos can land an ulp away.
    If that ever happens this fails loudly -- which is the point, because the
    alternative is `heuristic` drifting under a refactor that everything else
    in the suite would call a no-op."""
    for name, pack in _packs():
        qc = compute_costs(pack, free_flow(pack, CFG), CFG)
        for lat, lon in _dest_points(pack):
            got = heuristic(pack, qc, lat, lon)
            want = _heuristic_by_node_gather(pack, qc, lat, lon)
            assert got.tobytes() == want.tobytes(), (
                f"heuristic is not gather-invariant on {name} at ({lat}, {lon})")


REAL_PACKS = ["data/packs/berkeley_oakland", "data/packs/berkeley_small"]


@pytest.mark.parametrize("path", REAL_PACKS)
def test_heuristic_gather_invariance_on_real_pack(path):
    """The toys have a handful of nodes, and SIMD tails and kernel dispatch
    only diverge at scale, so the identity above is worth little until it is
    checked on a pack with thousands of nodes. Skips where the pack is not
    built -- `data/` is not committed."""
    if not os.path.isdir(path):
        pytest.skip(f"{path} not built")
    pack = GraphPack.load(path)
    qc = compute_costs(pack, free_flow(pack, CFG), CFG)
    for lat, lon in _dest_points(pack):
        got = heuristic(pack, qc, lat, lon)
        want = _heuristic_by_node_gather(pack, qc, lat, lon)
        assert got.tobytes() == want.tobytes()


def _format_golden() -> str:
    lines = ["GOLDEN: dict[str, str] = {"]
    for k, v in _all_digests().items():
        lines.append(f'    "{k}": "{v}",')
    lines.append("}")
    return "\n".join(lines)


if __name__ == "__main__":   # pragma: no cover
    print(_format_golden())
