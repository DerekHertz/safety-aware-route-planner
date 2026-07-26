"""Curated route alternatives: lambda sweep -> Jaccard dedup -> penalty-method
rerun for forced diversity.

- Sweep: run the search at lambda_fast (=0), lambda_balanced, lambda_safe.
- Dedup: two routes with edge-set Jaccard similarity above the config
  threshold are considered "the same"; the later (higher-lambda) candidate is
  re-run with the shared edges' arc costs inflated (penalty method) up to
  max_reruns times to force a genuinely distinct alternative, else dropped.
- safety_enabled=False: pure fastest path — a single "fast" route. (The
  unsafe counters are still computed and reported; the toggle only removes
  the penalty from the cost. Documented choice.)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyref.config import Config
from pyref.costs import QueryCosts, arc_cost
from pyref.graph import GraphPack
from pyref.search import PathResult, TurnTopo, shortest_path


@dataclass(frozen=True)
class Alternative:
    kind: str            # "fast" | "balanced" | "safe"
    lam: float
    result: PathResult


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def compute_alternatives(pack: GraphPack, qc: QueryCosts, topo: TurnTopo,
                         seeds: list[tuple[int, float]],
                         dests: list[tuple[int, float]],
                         h: np.ndarray | None,
                         cfg: Config,
                         safety_enabled: bool = True,
                         run=None) -> list[Alternative]:
    """`run(ac, h, seeds, dests)` performs one search — injectable so the
    facade can route the sweep through the C++ engine; defaults to pyref."""
    if run is None:
        run = lambda ac_, h_, s_, d_: shortest_path(topo, ac_, h_, s_, d_)
    alt_cfg = cfg["alternatives"]
    sweep = [("fast", float(alt_cfg["lambda_fast"]))]
    if safety_enabled:
        sweep += [("balanced", float(alt_cfg["lambda_balanced"])),
                  ("safe", float(alt_cfg["lambda_safe"]))]

    threshold = float(alt_cfg["jaccard_threshold"])
    factor = float(alt_cfg["penalty_factor"])
    max_reruns = int(alt_cfg["max_reruns"])
    out_edge = pack.turn_out_edge

    kept: list[Alternative] = []
    kept_edge_sets: list[set[int]] = []

    def unsafe_count(result: PathResult) -> int:
        return int(np.count_nonzero(qc.turn_unsafe_type[result.turn_ids]))

    for kind, lam in sweep:
        ac = arc_cost(pack, qc, lam)
        result = run(ac, h, seeds, dests)
        if result is None:
            continue
        edge_set = set(result.edges(out_edge))
        # Safety level of the true (un-inflated) optimum at this lambda: a
        # penalty-rerun alternative may not be WORSE than this, or the slot
        # is dropped. Without this guard, when the fastest route is already
        # the safest, the forced-diversity rerun would produce a detour with
        # MORE flagged maneuvers and label it "safe".
        base_unsafe = unsafe_count(result)

        reruns = 0
        while (any(jaccard(edge_set, ks) > threshold for ks in kept_edge_sets)
               and reruns < max_reruns):
            # penalty method: inflate the cost of every turn ENTERING an edge
            # already used by a kept route, then re-search
            shared = set().union(*kept_edge_sets) & edge_set
            mask = np.isin(out_edge, list(shared))
            ac = ac.copy()
            ac[mask] *= factor
            rerun = run(ac, h, seeds, dests)
            reruns += 1
            if rerun is None:
                break
            result = rerun
            edge_set = set(result.edges(out_edge))

        if any(jaccard(edge_set, ks) > threshold for ks in kept_edge_sets):
            continue  # still a duplicate after max_reruns -> drop
        if unsafe_count(result) > base_unsafe:
            continue  # diversified route is safety-worse -> drop the slot
        kept.append(Alternative(kind=kind, lam=lam, result=result))
        kept_edge_sets.append(edge_set)

    # Relabel by position AFTER dedup (kept is in ascending-lambda order):
    # the lowest-lambda survivor is "fast", the highest-lambda survivor is
    # "safe", anything between is "balanced". Example: when the balanced
    # sweep already finds the safest available path, the lambda_safe run
    # dedups away — the surviving route deserves the "safe" label.
    if safety_enabled and len(kept) > 1:
        labels = ["fast"] + ["balanced"] * (len(kept) - 2) + ["safe"]
        kept = [Alternative(kind=lbl, lam=a.lam, result=a.result)
                for lbl, a in zip(labels, kept)]
    return kept
