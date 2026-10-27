"""The frozen rule baseline (ADR-0019 decision 4), pinned case by case."""
from __future__ import annotations

import pytest

from planner.baseline import CandidateSummary, rule_choice, target_kind

FAST = CandidateSummary("fast", eta_s=600, unsafe_total=3)
BALANCED = CandidateSummary("balanced", eta_s=660, unsafe_total=1)
SAFE = CandidateSummary("safe", eta_s=720, unsafe_total=0)
ALL = [FAST, BALANCED, SAFE]


@pytest.mark.parametrize("tradeoff,kind", [
    (2, "safe"), (1, "balanced"), (0, "balanced"), (-1, "balanced"), (-2, "fast")])
def test_the_tradeoff_names_the_target(tradeoff, kind):
    assert target_kind(tradeoff) == kind
    assert rule_choice(tradeoff, ALL) == kind


def test_no_candidates_means_no_choice():
    assert rule_choice(2, []) is None


def test_an_end_that_is_not_a_candidate_steps_to_balanced():
    assert rule_choice(2, [FAST, BALANCED]) == "balanced"
    assert rule_choice(-2, [BALANCED, SAFE]) == "balanced"


def test_an_end_with_no_balanced_falls_to_the_other_end():
    assert rule_choice(2, [FAST]) == "fast"
    assert rule_choice(-2, [SAFE]) == "safe"


def test_balanced_missing_is_a_tie_that_goes_to_fewer_unsafe_actions():
    assert rule_choice(0, [FAST, SAFE]) == "safe"
    riskier_safe = CandidateSummary("safe", eta_s=720, unsafe_total=4)
    assert rule_choice(0, [FAST, riskier_safe]) == "fast"


def test_a_full_tie_goes_to_the_shorter_eta_then_to_safe():
    a = CandidateSummary("fast", eta_s=600, unsafe_total=1)
    b = CandidateSummary("safe", eta_s=650, unsafe_total=1)
    assert rule_choice(1, [a, b]) == "fast"
    c = CandidateSummary("safe", eta_s=600, unsafe_total=1)
    assert rule_choice(1, [a, c]) == "safe"
