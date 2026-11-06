"""S7: the Jev seam, batching, cache, threshold and gate. Offline: no test reaches the network."""

import logging
from collections.abc import Mapping
from typing import Any

import pytest

from gapcross.jev import (
    KINDS,
    Answer,
    AnswerCache,
    JevClient,
    OpenCrossing,
    Question,
    SettledCrossing,
    ask_route,
    cache_key,
    counted,
    evaluate_gate,
    question_name,
    run_gate,
)
from gapcross.transport import FixtureMissing, FixtureTransport, SourceUnavailable

VERSION = "jev-test-1"


def sure(choice: str, p: float = 0.97) -> Answer:
    """An answer for any of the three questions: `choice` at probability p, the rest of the mass elsewhere."""
    options = next(k for k in KINDS.values() if choice in k.criteria).criteria
    others = [o for o in options if o != choice]
    return Answer(choice, {choice: p, **{o: (1 - p) / len(others) for o in others}})


class FakeJev:
    """Counts calls and records what each call carried."""

    def __init__(self, model: str = "jev-fake", version: str = VERSION, script: Mapping[str, Answer] | None = None):
        self.model = model
        self.version = version
        self.script = script or {}
        self.calls: list[tuple[dict[str, Any], dict[str, Question]]] = []

    def ask(self, state, questions):
        self.calls.append((state, dict(questions)))
        def default(name: str) -> Answer:
            return sure(next(iter(KINDS[name.rpartition(".")[2]].criteria)))

        return {n: self.script.get(n) or default(n) for n in questions}, self.version


def crossings(*keys: str) -> list[OpenCrossing]:
    return [OpenCrossing(k, {"main_street": {"name": f"street {k}"}, "kind": "straight"}) for k in keys]


# --- batching ---------------------------------------------------------------------------------------


def test_one_request_per_route_carries_every_question_for_every_crossing():
    jev, cache = FakeJev(), AnswerCache(":memory:")
    answers, stats = ask_route(jev, cache, crossings("1+2/straight/E", "3+4/left_onto/N", "5+6/straight/S"))
    assert len(jev.calls) == 1
    state, questions = jev.calls[0]
    expected = {f"{k}.{kind}" for k in ("1+2/straight/E", "3+4/left_onto/N", "5+6/straight/S") for kind in KINDS}
    assert set(questions) == expected and len(expected) == 9
    assert set(answers) == expected
    assert set(state["crossings"]) == {"1+2/straight/E", "3+4/left_onto/N", "5+6/straight/S"}
    assert (stats.questions, stats.hits, stats.misses, stats.calls) == (9, 0, 9, 1)
    assert stats.latency_s >= 0 and stats.model_version == VERSION


def test_only_the_questions_a_crossing_left_open_are_asked():
    jev, cache = FakeJev(), AnswerCache(":memory:")
    ask_route(jev, cache, [OpenCrossing("1+2/straight/E", {"x": 1}, ("road_function",))])
    assert set(jev.calls[0][1]) == {"1+2/straight/E.road_function"}


def test_question_wording_matches_the_design():
    assert {k: set(v.criteria) for k, v in KINDS.items()} == {
        "approach_control": {"stops_for_us", "does_not_stop", "cannot_tell"},
        "road_function": {"through_street", "local", "ambiguous"},
        "records_conflict": {"agree", "conflict", "insufficient"},
    }
    assert question_name("1+2/straight/E", "road_function") == "1+2/straight/E.road_function"


# --- cache ------------------------------------------------------------------------------------------


def test_cache_hit_skips_jev():
    jev, cache = FakeJev(), AnswerCache(":memory:")
    first, _ = ask_route(jev, cache, crossings("a", "b"))
    second, stats = ask_route(jev, cache, crossings("a", "b"))
    assert len(jev.calls) == 1
    assert second == first
    assert (stats.hits, stats.misses, stats.calls, stats.latency_s) == (6, 0, 0, 0.0)
    assert stats.model_version == VERSION


def test_a_partial_hit_sends_only_the_new_crossing():
    jev, cache = FakeJev(), AnswerCache(":memory:")
    ask_route(jev, cache, crossings("a"))
    _, stats = ask_route(jev, cache, crossings("a", "b"))
    assert len(jev.calls) == 2
    state, questions = jev.calls[1]
    assert set(state["crossings"]) == {"b"} and set(questions) == {f"b.{k}" for k in KINDS}
    assert (stats.hits, stats.misses) == (3, 3)


def test_changed_evidence_model_or_question_misses():
    cache = AnswerCache(":memory:")
    ask_route(FakeJev(), cache, crossings("a"))
    changed = [OpenCrossing("a", {"main_street": {"name": "other"}, "kind": "straight"})]
    other_model = FakeJev(model="jev-other")
    assert ask_route(FakeJev(), cache, changed)[1].misses == 3
    assert ask_route(other_model, cache, crossings("a"))[1].misses == 3
    assert cache_key("m", "a", "road_function", {"x": 1}) != cache_key("m", "a", "road_function", {"x": 2})
    assert cache_key("m", "a", "road_function", {"x": 1}) == cache_key("m", "a", "road_function", {"x": 1})


def test_cache_persists_on_disk(tmp_path):
    path = tmp_path / "var" / "jev_cache.sqlite"
    ask_route(FakeJev(), AnswerCache(path), crossings("a"))
    jev = FakeJev()
    _, stats = ask_route(jev, AnswerCache(path), crossings("a"))
    assert jev.calls == [] and stats.hits == 3


def test_jev_unavailable_raises_and_caches_nothing():
    class Down(FakeJev):
        def ask(self, state, questions):
            raise SourceUnavailable("down")

    cache = AnswerCache(":memory:")
    with pytest.raises(SourceUnavailable):
        ask_route(Down(), cache, crossings("a"))
    assert ask_route(FakeJev(), cache, crossings("a"))[1].misses == 3


# --- threshold --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("p", "expected"),
    [(1.0, "local"), (0.95, "local"), (0.9, "local"), (0.8999, None), (0.5, None), (0.34, None)],
)
def test_answer_counts_only_at_or_above_point_nine(p, expected):
    assert counted(Answer("local", {"local": p, "through_street": (1 - p) / 2, "ambiguous": (1 - p) / 2})) == expected


# --- gate arithmetic (expected numbers worked out by hand) -------------------------------------------


def test_gate_fails_when_jev_confidently_disagrees():
    rules = {
        "a.approach_control": "stops_for_us",   # jev stops_for_us .95        -> agree
        "a.road_function": "through_street",    # jev through_street .92      -> agree
        "b.approach_control": "does_not_stop",  # jev stops_for_us .97        -> DISAGREE
        "b.road_function": "local",             # jev local .60               -> abstain (below .9)
        "c.approach_control": "stops_for_us",   # jev cannot_tell .99         -> abstain (cannot tell)
    }
    answers = {
        "a.approach_control": sure("stops_for_us", 0.95),
        "a.road_function": sure("through_street", 0.92),
        "b.approach_control": sure("stops_for_us", 0.97),
        "b.road_function": sure("local", 0.60),
        "c.approach_control": sure("cannot_tell", 0.99),
    }
    result = evaluate_gate(rules, answers, VERSION, settled=3)
    assert (result.settled, result.compared, result.agree, result.disagree, result.abstain) == (3, 3, 2, 1, 2)
    assert not result.passed
    assert result.disagreements == (("b.approach_control", "does_not_stop", "stops_for_us", 0.97),)


def test_gate_passes_with_no_confident_disagreement():
    rules = {"a.approach_control": "stops_for_us", "b.approach_control": "does_not_stop", "b.road_function": "local"}
    answers = {
        "a.approach_control": sure("stops_for_us", 0.9),    # exactly at threshold: counts, agrees
        "b.approach_control": sure("does_not_stop", 0.91),  # agrees
        "b.road_function": sure("through_street", 0.89),    # would disagree but below threshold: abstain
    }
    result = evaluate_gate(rules, answers, VERSION, settled=2)
    assert (result.compared, result.agree, result.disagree, result.abstain) == (2, 2, 0, 1)
    assert result.passed


def test_gate_with_nothing_compared_is_not_a_pass():
    rules = {"a.road_function": "local"}
    result = evaluate_gate(rules, {"a.road_function": sure("local", 0.5)}, VERSION, settled=1)
    assert (result.compared, result.agree, result.abstain) == (0, 0, 1)
    assert not result.passed


def test_run_gate_logs_counts_stores_result_and_uses_the_cache(caplog):
    settled = [
        SettledCrossing("a", {"e": 1}, {"approach_control": "stops_for_us", "road_function": "through_street"}),
        SettledCrossing("b", {"e": 2}, {"approach_control": "does_not_stop"}),
    ]
    script = {
        "a.approach_control": sure("stops_for_us"),
        "a.road_function": sure("through_street"),
        "b.approach_control": sure("does_not_stop"),
    }
    cache = AnswerCache(":memory:")
    jev = FakeJev(script=script)
    with caplog.at_level(logging.INFO, logger="gapcross.jev"):
        result = run_gate(jev, cache, settled, min_agree=1)
    assert result.passed and (result.settled, result.compared, result.agree) == (2, 3, 3)
    assert len(jev.calls) == 1 and set(jev.calls[0][1]) == set(script)
    assert "jev gate PASS" in caplog.text and "settled=2 compared=3 agree=3 disagree=0 abstain=0" in caplog.text
    assert cache.gate_passed(VERSION) and not cache.gate_passed("jev-other")
    run_gate(jev, cache, settled, min_agree=1)  # second run: all hits, Jev not called again
    assert len(jev.calls) == 1


def test_no_gate_run_means_the_gate_has_not_passed():
    assert not AnswerCache(":memory:").gate_passed(VERSION)


# --- the client (recorded and failing transports, never live) ----------------------------------------


class Canned:
    def __init__(self, body):
        self.body, self.seen = body, []

    def get_json(self, service, request, fetch):
        self.seen.append((service, request))
        return self.body


def response(choice="local", p=0.97):
    return {"model": "jev-9.9.9", "answers": {"a.road_function": {
        "type": "choice", "choice": choice, "confidence": 1.0,
        "probabilities": {choice: p, **{o: (1 - p) / 2 for o in KINDS["road_function"].criteria if o != choice}}}},
        "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_client_builds_the_request_and_parses_the_answer():
    transport = Canned(response())
    client = JevClient(transport, model="jev-latest")
    q = {"a.road_function": Question("why", KINDS["road_function"].criteria)}
    answers, version = client.ask({"crossings": {}}, q)
    assert version == "jev-9.9.9" and answers["a.road_function"].choice == "local"
    service, request = transport.seen[0]
    assert service == "jev" and request["model"] == "jev-latest"
    assert request["questions"]["a.road_function"]["type"] == "choice"


def test_client_rejects_an_answer_outside_the_options():
    client = JevClient(Canned(response(choice="banana")))
    with pytest.raises(SourceUnavailable):
        client.ask({}, {"a.road_function": Question("why", KINDS["road_function"].criteria)})


def test_client_without_a_key_is_unavailable_and_never_calls_out(monkeypatch):
    from gapcross.transport import LiveTransport

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    client = JevClient(LiveTransport())
    with pytest.raises(SourceUnavailable, match="TYPESAFE_API_KEY is not set"):
        client.ask({}, {"a.road_function": Question("why", KINDS["road_function"].criteria)})


def test_fixture_adapter_raises_for_an_unrecorded_request(tmp_path):
    client = JevClient(FixtureTransport(tmp_path))
    with pytest.raises(FixtureMissing):
        client.ask({}, {"a.road_function": Question("why", KINDS["road_function"].criteria)})


def test_prerun_gates_then_fills_the_cache_for_open_crossings(caplog):
    from gapcross.prerun import prerun

    settled = [SettledCrossing("s", {"e": 0}, {"road_function": "local"})]
    open_ = crossings(*[f"o{i}" for i in range(20)])  # 20 crossings -> two requests of at most 15
    cache = AnswerCache(":memory:")
    jev = FakeJev(script={"s.road_function": sure("local")})
    with caplog.at_level(logging.INFO, logger="gapcross.jev"):
        result = prerun(jev, cache, settled, open_, min_agree=1)
    assert result.gate.passed and len(jev.calls) == 1 + 2
    assert [len(c[0]["crossings"]) for c in jev.calls] == [1, 15, 5]
    assert "open=20 open_hits=0 open_misses=60 open_calls=2" in caplog.text
    assert prerun(jev, cache, settled, open_, min_agree=1).open_batches[0].calls == 0  # rerun: cache only
    assert len(jev.calls) == 3


def test_gate_over_no_settled_crossings_fails_and_is_stored():
    cache = AnswerCache(":memory:")
    result = run_gate(FakeJev(), cache, [])
    assert not result.passed and result.settled == 0 and not cache.gate_passed(VERSION)
