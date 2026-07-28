"""Route metrics: distance, ETA, unsafe-action counts. Pure Python/numpy —
never inside the engines; applied to the turn-id path they return."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyref.costs import UNSAFE_CROSSING, UNSAFE_LEFT, QueryCosts
from pyref.graph import GraphPack
from pyref.search import PathResult


@dataclass(frozen=True)
class RouteMetrics:
    distance_m: float
    eta_s: float
    unprotected_left: int
    uncontrolled_crossing: int

    @property
    def unsafe_total(self) -> int:
        return self.unprotected_left + self.uncontrolled_crossing


def compute_metrics(pack: GraphPack, qc: QueryCosts, result: PathResult,
                    frac_origin: float = 0.0, frac_dest: float = 1.0) -> RouteMetrics:
    """frac_origin / frac_dest: snap fractions along the first / last edge.
    The first edge contributes its tail (1 - frac_origin) share, the last its
    head frac_dest share; a single-edge route contributes the difference."""
    edges = result.edges(pack.turn_out_edge)
    lengths = pack.edge_length_m[edges]
    times = qc.edge_time_s[edges]

    share = np.ones(len(edges), dtype=np.float64)
    share[0] -= frac_origin
    share[-1] -= (1.0 - frac_dest)

    distance = float(np.sum(lengths * share))
    eta = float(np.sum(times * share))

    kinds = (qc.turn_unsafe_type[result.turn_ids] if len(result.turn_ids)
             else np.array([], dtype=np.uint8))
    return RouteMetrics(
        distance_m=distance,
        eta_s=eta,
        unprotected_left=int(np.count_nonzero(kinds == UNSAFE_LEFT)),
        uncontrolled_crossing=int(np.count_nonzero(kinds == UNSAFE_CROSSING)),
    )
