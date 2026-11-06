"""S4: local search and comparison.

Rules 3-7 are checked against values computed here, never against the code's own arithmetic or
its own "search complete" flag (properties.md). The deadline uses an injected clock.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gapcross.assess import FixedAdvisor
from gapcross.compare import TEMPLATES, Assessed, budget_s, compare, decide
from gapcross.sources import LatLon, Sources, offline_sources
from gapcross.trips import load_trips
from gapcross.types import Assessment, Crossing, Evidence

DEMO = "bowl-west-to-campus"
SACRAMENTO_AT_OREGON = "53050628+240469698/straight/E"
SACRAMENTO_AT_WARD = "53050627+240469515/straight/E"
SRC = Path(__file__).resolve().parents[1] / "src" / "gapcross"


@pytest.fixture(scope="module")
def trip():
    return load_trips()[DEMO]


@pytest.fixture(scope="module")
def result(trip):
    return compare(trip, offline_sources(), FixedAdvisor())


# --- The check: the search finds Dohr/Ward on its own ------------------------------------------

def test_search_finds_ward_without_being_told(result):
    assert result.outcome == "improvement"
    keys = {c.key for c in result.alternative.crossings}
    assert SACRAMENTO_AT_WARD in keys
    assert SACRAMENTO_AT_OREGON not in keys
    assert SACRAMENTO_AT_OREGON in {c.key for c in result.default.crossings}


def test_rule_3_shown_alternative_beats_default_in_the_worst_case(result):
    """Oracle: counts computed here from the two routes' crossings. A crossing on both routes
    is the same crossing with the same truth, so only the crossings that differ are compared."""
    default = {c.key: c.assessment.status for c in result.default.crossings}
    alt = {c.key: c.assessment.status for c in result.alternative.crossings}
    default_known = sum(s == "flagged" for k, s in default.items() if k not in alt)
    alt_worst = sum(s in ("flagged", "unknown") for k, s in alt.items() if k not in default)
    assert alt_worst < default_known


def test_rule_4_shown_alternative_is_within_budget(result):
    budget = min(0.2 * result.default.eta_s, 300.0)
    assert result.budget_s == pytest.approx(budget)
    assert result.alternative.eta_s - result.default.eta_s <= budget
    assert result.added_s == pytest.approx(result.alternative.eta_s - result.default.eta_s)


def test_rule_5_every_crossing_on_the_alternative_is_assessed(result):
    assert result.alternative.crossings
    for c in result.alternative.crossings:
        assert c.assessment.status in ("flagged", "unflagged", "unknown"), c.key


def test_rule_6_alternative_crosses_where_and_how_intended(result):
    ward = next(c for c in result.alternative.crossings if c.key == SACRAMENTO_AT_WARD)
    oregon = next(c for c in result.default.crossings if c.key == SACRAMENTO_AT_OREGON)
    assert ward.kind == "straight"
    turn = (ward.approach_bearing - oregon.approach_bearing + 540) % 360 - 180
    assert abs(turn) <= 45


def test_rule_6_a_candidate_that_misses_its_target_is_dropped(trip):
    """Every candidate request returns the default route, which never crosses at a candidate."""
    real = offline_sources()
    default = real.routes.route([trip.origin, *trip.example_via, trip.destination])

    class Stubborn:
        def route(self, waypoints):
            return default

    out = compare(trip, Sources(Stubborn(), real.records), FixedAdvisor())
    assert out.alternative is None
    assert out.outcome == "no_improvement"


# --- Rule 7 and the deadline, with an injected clock --------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _ticking(sources, clock, seconds_per_route):
    class Ticking:
        calls = 0

        def route(self, waypoints):
            Ticking.calls += 1
            if Ticking.calls > 1:  # the default route is fetched before the deadline starts
                clock.now += seconds_per_route
            return sources.routes.route(waypoints)

    return Sources(Ticking(), sources.records), Ticking


def test_rule_7_deadline_before_any_candidate_is_incomplete(trip):
    clock = FakeClock()
    sources, ticking = _ticking(offline_sources(), clock, seconds_per_route=11.0)
    out = compare(trip, sources, FixedAdvisor(), clock=clock, deadline_s=10.0)
    assert clock.now > 10.0  # observed here: the deadline passed during the search
    assert ticking.calls - 1 == 1  # exactly one candidate was routed, then the search stopped
    assert out.search_complete is False
    assert out.outcome != "no_improvement"
    if out.alternative is None:
        assert out.outcome == "incomplete"


def test_rule_7_an_incomplete_search_is_never_no_improvement():
    default = _assessed(["flagged"], eta=600)
    worse = _assessed(["flagged", "flagged"], eta=610, prefix="b")
    for found in ([], [worse]):
        d = decide(default, found, search_complete=False)
        assert d.outcome == "incomplete"
        assert d.search_complete is False


def test_improvement_found_before_the_deadline_is_kept():
    default = _assessed(["flagged"], eta=600)
    better = _assessed(["unflagged"], eta=620, prefix="b")
    d = decide(default, [better], search_complete=False)
    assert d.outcome == "improvement" and d.chosen == 0 and d.search_complete is False


# --- Rules 3-5 on hand-made routes --------------------------------------------------------------

def test_rule_3_unknowns_count_as_flags_for_the_alternative():
    default = _assessed(["flagged"], eta=600)
    one_unknown = _assessed(["unknown"], eta=600, prefix="b")
    assert decide(default, [one_unknown], search_complete=True).outcome == "no_improvement"


def test_rule_3_shared_crossings_cancel():
    shared = _crossing("shared", "unknown")
    default = Assessed((_crossing("a0", "flagged"), shared), eta_s=600)
    alt = Assessed((_crossing("b0", "unflagged"), shared), eta_s=610)
    assert decide(default, [alt], search_complete=True).outcome == "improvement"


def test_rule_4_over_budget_is_not_shown():
    default = _assessed(["flagged"], eta=600)
    budget = min(0.2 * 600, 300.0)  # 120 s, computed here
    slow = _assessed(["unflagged"], eta=600 + budget + 1, prefix="b")
    d = decide(default, [slow], search_complete=True)
    assert d.outcome == "over_budget" and d.chosen is None
    assert budget_s(600) == pytest.approx(budget)
    assert budget_s(3000) == pytest.approx(300.0)


def test_rule_5_a_candidate_with_a_failed_check_is_never_shown():
    default = _assessed(["flagged"], eta=600)
    failed = Assessed((_crossing("b0", "check_failed"),), eta_s=600, failed=True)
    d = decide(default, [failed], search_complete=True)
    assert d.chosen is None


def test_ranking_prefers_fewer_worst_case_then_known_then_eta():
    default = _assessed(["flagged", "flagged", "flagged"], eta=600)
    a = _assessed(["unknown"], eta=610, prefix="a")
    b = _assessed(["unflagged"], eta=640, prefix="b")
    c = _assessed(["unflagged"], eta=620, prefix="c")
    assert decide(default, [a, b, c], search_complete=True).chosen == 2


def test_default_without_flags_needs_no_search():
    default = _assessed(["unflagged", "unknown"], eta=600)
    assert decide(default, [], search_complete=True).outcome == "no_improvement"


def test_failed_default_is_check_failed():
    default = Assessed((_crossing("a0", "check_failed"),), eta_s=600, failed=True)
    assert decide(default, [], search_complete=True).outcome == "check_failed"


# --- Rule 14 ------------------------------------------------------------------------------------

UNSAFE = re.compile(r"\bsaf(e|er|est|ety|ely)\b", re.IGNORECASE)


def test_rule_14_no_template_or_explanation_says_safe(result):
    for name, text in TEMPLATES.items():
        assert not UNSAFE.search(text), name
    for module in ("assess.py", "compare.py", "search.py"):
        for literal in re.findall(r'"([^"\n]*)"', (SRC / module).read_text()):
            assert not UNSAFE.search(literal), (module, literal)
    texts = [result.summary] + [c.assessment.explanation for r in (result.default, result.alternative)
                                for c in r.crossings]
    for text in texts:
        assert not UNSAFE.search(text), text


# --- helpers ------------------------------------------------------------------------------------

def _crossing(key, status):
    return Crossing(
        key=f"{key}/straight/E", main_street="Main Street", kind="straight", approach_bearing=90.0,
        along_m=0.0, location=LatLon(lat=37.86, lon=-122.27),
        assessment=Assessment(status=status, explanation="x", evidence=[
            Evidence(source="osm", ref="node/1", claim="c", retrieved_at=datetime(2026, 11, 6, tzinfo=UTC),
                     is_lead=False)]),
    )


def _assessed(statuses, eta, prefix="a"):
    return Assessed(tuple(_crossing(f"{prefix}{i}", s) for i, s in enumerate(statuses)), eta_s=eta)
