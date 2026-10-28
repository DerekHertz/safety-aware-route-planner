"""The frozen rule baseline for the Choice (ADR-0019 decision 4).

Committed before `evals/requests.jsonl` existed, and not to be tuned against it:
it is the number the Jev Choice has to beat, so moving it after seeing results
would make that comparison meaningless. Change it only by a dated ADR-0019
amendment that says why.

The rule, as decision 4 states it:

- a strong safety Tradeoff (+2) picks `safe`, a strong speed Tradeoff (-2)
  picks `fast`, anything else picks `balanced`;
- if that route is not a Candidate, step toward `balanced`;
- ties go to fewer unsafe actions.

"Step toward balanced" from `balanced` itself has two equally near neighbours,
`fast` and `safe`; that is the tie, and it goes to the one with fewer unsafe
actions (then the shorter ETA, then `safe`, so the answer is always defined).
From `safe` or `fast`, the step is to `balanced`, and if that is not a
Candidate either, to the far end, which is the only one left.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

ORDER = ("fast", "balanced", "safe")


@dataclass(frozen=True)
class CandidateSummary:
    """What the rule reads of a Candidate Route."""
    kind: str             # "fast" | "balanced" | "safe"
    eta_s: float
    unsafe_total: int


def target_kind(tradeoff: int) -> str:
    if tradeoff >= 2:
        return "safe"
    if tradeoff <= -2:
        return "fast"
    return "balanced"


def rule_choice(tradeoff: int, candidates: Sequence[CandidateSummary]) -> str | None:
    """The kind the rule picks among `candidates`, or None if there are none."""
    by_kind = {c.kind: c for c in candidates}
    if not by_kind:
        return None
    target = target_kind(tradeoff)
    if target in by_kind:
        return target
    if target != "balanced" and "balanced" in by_kind:
        return "balanced"
    # Either the target was balanced (two neighbours, a tie) or balanced is out
    # too (one end left). Both reduce to: fewest unsafe actions among the rest.
    rest = [by_kind[k] for k in ORDER if k in by_kind]
    return min(rest, key=lambda c: (c.unsafe_total, c.eta_s, -ORDER.index(c.kind))).kind
