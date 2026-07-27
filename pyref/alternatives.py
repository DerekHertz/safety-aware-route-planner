"""Curated route alternatives: lambda sweep -> Jaccard dedup -> penalty-method
rerun for forced diversity.

- Sweep: run the search at lambda_fast (=0), lambda_balanced, lambda_safe.
- Detour budget: "how far out of my way for a safer crossing", as a fraction
  of the fastest route's time. It works both ways. When the lambda sweep will
  not buy a detour the budget allows, a hard-avoid run forces the issue
  (_avoid_within_budget); when the sweep produces a route longer than the
  budget allows, that alternative is dropped (_within_budget). Lambda alone
  cannot express either: it trades penalty against time at a fixed exchange
  rate and has no notion of how far the user is actually willing to go.
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
from pyref.costs import UNSAFE_NONE, QueryCosts, arc_cost
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


def travel_time_fn(pack: GraphPack, qc: QueryCosts,
                   seeds: list[tuple[int, float]],
                   dests: list[tuple[int, float]]):
    """Exact travel time of a candidate path, in seconds.

    At lambda 0 the arc costs ARE travel time, and the search's own seed and
    destination costs carry the partial first/last edge shares from snapping.
    Summing them reproduces the ETA pyref.metrics reports, which matters: the
    detour budget is a promise about the number the user sees.
    """
    time_ac = arc_cost(pack, qc, 0.0)
    seed_cost = dict(seeds)
    dest_cost = dict(dests)

    def travel_time_s(result: PathResult) -> float:
        return (seed_cost[result.first_edge]
                + float(time_ac[result.turn_ids].sum())
                + dest_cost[result.dest_edge])

    return travel_time_s


def compute_alternatives(pack: GraphPack, qc: QueryCosts, topo: TurnTopo,
                         seeds: list[tuple[int, float]],
                         dests: list[tuple[int, float]],
                         h: np.ndarray | None,
                         cfg: Config,
                         safety_enabled: bool = True,
                         run=None,
                         detour_budget_pct: float | None = None) -> list[Alternative]:
    """`run(ac, h, seeds, dests)` performs one search — injectable so the
    facade can route the sweep through the C++ engine; defaults to pyref.

    `detour_budget_pct` defaults to the config value; 0 disables the hard
    avoid and leaves the plain lambda sweep in charge.
    """
    if run is None:
        run = lambda ac_, h_, s_, d_: shortest_path(topo, ac_, h_, s_, d_)
    alt_cfg = cfg["alternatives"]
    if detour_budget_pct is None:
        detour_budget_pct = float(alt_cfg["detour_budget_pct"])
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
    fast_time_s: float | None = None
    travel_time_s = travel_time_fn(pack, qc, seeds, dests)

    def unsafe_count(result: PathResult) -> int:
        return int(np.count_nonzero(qc.turn_unsafe_type[result.turn_ids]))

    for kind, lam in sweep:
        ac = arc_cost(pack, qc, lam)
        result = run(ac, h, seeds, dests)
        if result is None:
            continue
        if fast_time_s is None:      # the lambda_fast run: the time baseline
            fast_time_s = travel_time_s(result)
        if kind == "safe" and detour_budget_pct > 0 and unsafe_count(result) > 0:
            clean = _avoid_within_budget(pack, qc, cfg, run, h, seeds, dests,
                                         lam, fast_time_s, detour_budget_pct,
                                         unsafe_count(result), travel_time_s)
            if clean is not None:
                result, ac = clean
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

    # Enforce the budget as a ceiling. The fastest route is always kept — it
    # defines the baseline and by construction costs nothing extra.
    if fast_time_s is not None and len(kept) > 1:
        limit = (1.0 + detour_budget_pct) * fast_time_s
        kept = [kept[0]] + [a for a in kept[1:]
                            if travel_time_s(a.result) <= limit]

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


def _avoid_within_budget(pack: GraphPack, qc: QueryCosts, cfg: Config, run,
                         h, seeds, dests, lam: float, fast_time_s: float,
                         budget_pct: float, baseline_unsafe: int,
                         travel_time_s) -> tuple[PathResult, np.ndarray] | None:
    """The safe slot's hard-avoid attempt: "go two blocks over to the light".

    The lambda sweep only trades penalty against time, so a long enough detour
    always loses however high lambda goes — which is why the planner would
    still hand you the unprotected left. This run makes every counted unsafe
    maneuver prohibitively expensive instead, then accepts the answer only if
    it fits the caller's time budget.

    Returns (result, arc_costs) when the avoiding route is both safer and
    affordable, else None so the caller keeps the plain lambda_safe route.
    """
    flagged = qc.turn_unsafe_type != UNSAFE_NONE
    if not flagged.any():
        return None
    surcharge = float(cfg["alternatives"]["avoid_surcharge_s"])
    ac = arc_cost(pack, qc, lam) + np.where(flagged, surcharge, 0.0)
    result = run(ac, h, seeds, dests)
    if result is None:
        return None
    if int(np.count_nonzero(qc.turn_unsafe_type[result.turn_ids])) >= baseline_unsafe:
        return None      # no cleaner: every route has to take one of these
    if travel_time_s(result) > (1.0 + budget_pct) * fast_time_s:
        return None      # cleaner, but further out of the way than asked for
    return result, ac
