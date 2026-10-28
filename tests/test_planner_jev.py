"""Jev over HTTP, replay, and the parse questions' interpretation."""
from __future__ import annotations

import asyncio
import datetime
import json

import httpx
import pytest

from planner.jev import HttpJev, JevError, RecordingJev, ReplayJev, request_body, request_key
from planner.parse import departure_time, interpret, parse_questions, parse_state, split_clauses
from tests.helpers.planner_fakes import parse_answers

REPLY = {"model": "jev-1.14.0", "usage": {"input_tokens": 1200, "output_tokens": 9},
         "answers": {"q": {"type": "noul", "noul": 0.97}}}
QUESTIONS = {"q": {"type": "noul", "instructions": "Is it?"}}


def _http(handler) -> HttpJev:
    return HttpJev("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_http_jev_sends_the_pinned_model_and_reads_the_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=REPLY)

    reply = asyncio.run(_http(handler).ask({"request": "x"}, QUESTIONS))
    assert seen["body"]["model"] == "jev-1.14.0" and seen["auth"] == "Bearer k"
    assert reply.noul("q") == 0.97 and reply.input_tokens == 1200
    assert reply.cost_usd == pytest.approx(1200 * 0.042e-6)


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json={"detail": "boom"}),
    lambda r: httpx.Response(200, json={"answers": {}}),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
])
def test_every_http_failure_is_a_jev_error(handler):
    with pytest.raises(JevError):
        asyncio.run(_http(handler).ask("s", QUESTIONS))


def test_a_reply_missing_an_answer_is_a_jev_error():
    reply = asyncio.run(_http(lambda r: httpx.Response(200, json=REPLY)).ask("s", QUESTIONS))
    with pytest.raises(JevError):
        reply.choice("q")


def test_record_then_replay_is_exact_and_a_changed_question_misses(tmp_path):
    path = tmp_path / "rec.jsonl"
    live = _http(lambda r: httpx.Response(200, json=REPLY))
    asyncio.run(RecordingJev(live, path).ask("s", QUESTIONS))
    replay = ReplayJev.load(path)
    assert asyncio.run(replay.ask("s", QUESTIONS)).noul("q") == 0.97
    with pytest.raises(JevError):
        asyncio.run(replay.ask("s", {"q": {"type": "noul", "instructions": "Is it??"}}))
    assert len(replay.misses) == 1


def test_request_key_ignores_key_order():
    a = request_body({"a": 1, "b": 2}, QUESTIONS)
    b = request_body({"b": 2, "a": 1}, QUESTIONS)
    assert request_key(a) == request_key(b)


def test_clauses_split_on_punctuation_and_conjunctions():
    assert split_clauses("Safest way, but avoid the freeway and no lefts!") == [
        "Safest way", "avoid the freeway", "no lefts"]
    assert len(split_clauses(", ".join(["abc"] * 20))) == 8


def test_interpret_reads_each_answer():
    state = parse_state("no lefts and avoid tolls")
    questions = parse_questions(state)
    assert set(questions) >= {"no_unprotected_lefts", "tradeoff", "detour", "departure",
                              "beyond", "clause_0", "clause_1"}
    from planner.jev import JevReply
    answers = parse_answers(lefts=0.8, all_lefts=0.7, tradeoff=-1, detour="pct_10",
                            beyond=0.6, clause_yes=frozenset({1}))(state, questions)
    p = interpret(state, JevReply(answers, "jev-1.14.0", 1, 1.0))
    assert p.labels() == {"no_unprotected_lefts": True, "no_uncontrolled_crossings": False,
                          "tradeoff": -1, "detour": "pct_10", "departure": "now",
                          "unsupported": True}
    assert p.narrowed_all_lefts and p.unsupported == ("avoid tolls",)


def test_departure_buckets_resolve_from_the_client_clock():
    fri_evening = datetime.datetime(2026, 10, 30, 18, 0)
    assert departure_time("weekday_am_peak", fri_evening) == datetime.datetime(2026, 11, 2, 8)
    assert departure_time("night", fri_evening) == datetime.datetime(2026, 10, 31, 2)
    assert departure_time("off_peak", fri_evening) == datetime.datetime(2026, 10, 31, 13)
    assert departure_time("now", fri_evening) is None
    assert departure_time("other", fri_evening) is None
