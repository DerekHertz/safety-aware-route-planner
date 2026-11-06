"""The Jev seam: typed questions about a route's open crossings, an answer cache, and the gate.

Jev is TypeSafe's System One model. It reads records as text (ADR-0003) and answers three
choice questions per crossing. Everything here is plumbing around the judgments: the batching
(one request per route), the cache (so hits skip Jev), the 0.9 threshold that turns an answer
into a value or unknown, and the gate that decides whether Jev's answers may count at all.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from .transport import FixtureTransport, LiveTransport, RecordingTransport, SourceUnavailable, Transport

log = logging.getLogger("gapcross.jev")

API_URL = "https://api.typesafe.ai/v1/systemone"
API_KEY_ENV = "TYPESAFE_API_KEY"
DEFAULT_MODEL = "jev-1.14.0"  # pinned: an alias could change the model under the cache (ADR-0003)
MIN_AGREE = 10  # agreements the gate needs; 2 of 27 compared on the first corridor run (S7)
THRESHOLD = 0.9  # a starting value, to be checked against the gate (concrete.md)
MAX_CROSSINGS_PER_REQUEST = 15  # keeps one request well inside the model's context length
CACHE_PATH = Path(__file__).resolve().parents[2] / "var" / "jev_cache.sqlite"


@dataclass(frozen=True)
class Question:
    """A choice question. `criteria` maps each option to what it means."""

    instructions: str
    criteria: Mapping[str, str]

    def wire(self) -> dict[str, Any]:
        return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.criteria)}


@dataclass(frozen=True)
class Answer:
    choice: str
    probabilities: Mapping[str, float]

    @property
    def probability(self) -> float:
        return self.probabilities[self.choice]


class Jev(Protocol):
    model: str  # the model name requested; part of every cache key

    def ask(self, state: dict[str, Any], questions: Mapping[str, Question]) -> tuple[dict[str, Answer], str]:
        """Answers by question name, and the model version that answered. Raises SourceUnavailable."""
        ...


# --- the three questions (concrete.md "Jev questions") -------------------------------------------


@dataclass(frozen=True)
class QuestionKind:
    template: str  # {key} is the crossing key
    criteria: Mapping[str, str]
    abstains: frozenset[str]  # options that say "Jev cannot tell"; never compared in the gate


KINDS: dict[str, QuestionKind] = {
    "approach_control": QuestionKind(
        template=(
            'The driver follows the route through the crossing {key}, whose records are at `crossings["{key}"]`. '
            "Using only those records, does the main street's traffic that the driver must cross or turn "
            "left across have to stop for the driver's approach, through an all-way stop or a signal? "
            "A stop sign facing only the driver does not count."
        ),
        criteria={
            "stops_for_us": "The records show the main street's relevant traffic is controlled so it stops for the driver.",
            "does_not_stop": "The records show, or clearly imply, that the main street's traffic does not stop for the driver.",
            "cannot_tell": "The records do not settle whether the main street's traffic stops for the driver.",
        },
        abstains=frozenset({"cannot_tell"}),
    ),
    "road_function": QuestionKind(
        template=(
            'At the crossing {key}, whose records are at `crossings["{key}"]`, is the street the driver '
            "crosses or turns onto a through street carrying traffic past the neighborhood, or a local street?"
        ),
        criteria={
            "through_street": "The records positively show a street that carries through traffic.",
            "local": "The records show a street that serves only its own neighborhood.",
            "ambiguous": "The records do not establish the street's role.",
        },
        abstains=frozenset({"ambiguous"}),
    ),
    "records_conflict": QuestionKind(
        template=(
            'At the crossing {key}, whose records are at `crossings["{key}"]`, do the sources in the records '
            "agree about how traffic is controlled and which street is the main street?"
        ),
        criteria={
            "agree": "The sources are consistent with each other.",
            "conflict": "At least two sources contradict each other.",
            "insufficient": "There is too little in the records to compare sources.",
        },
        abstains=frozenset({"insufficient"}),
    ),
}


def question_name(key: str, kind: str) -> str:
    return f"{key}.{kind}"


def split_name(name: str) -> tuple[str, str]:
    key, _, kind = name.rpartition(".")
    return key, kind


def build_question(key: str, kind: str) -> Question:
    spec = KINDS[kind]
    return Question(spec.template.format(key=key), spec.criteria)


def counted(answer: Answer, threshold: float = THRESHOLD) -> str | None:
    """The chosen option if its probability reaches the threshold, else None (unknown)."""
    return answer.choice if answer.probability >= threshold else None


# --- adapters --------------------------------------------------------------------------------------


class JevClient:
    """Jev over the TypeSafe API, through a Transport so it can be live, recorded or replayed."""

    def __init__(self, transport: Transport, model: str = DEFAULT_MODEL, url: str = API_URL,
                 api_key_env: str = API_KEY_ENV):
        self.transport = transport
        self.model = model
        self.url = url
        self.api_key_env = api_key_env

    def ask(self, state: dict[str, Any], questions: Mapping[str, Question]) -> tuple[dict[str, Answer], str]:
        request = {"model": self.model, "state": state, "questions": {n: q.wire() for n, q in questions.items()}}
        body = self.transport.get_json("jev", request, lambda: self._post(request))
        return _parse_response(body, questions)

    def _post(self, request: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get(self.api_key_env)  # read by name; never logged or put in an error
        if not key:
            raise SourceUnavailable(f"{self.api_key_env} is not set")
        for attempt in range(3):
            try:
                response = httpx.post(self.url, json=request, timeout=60, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as error:
                raise SourceUnavailable(f"Jev request failed: {type(error).__name__}") from error
            if response.status_code in (429, 529) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code != 200:
                raise SourceUnavailable(f"Jev answered HTTP {response.status_code}: {response.text[:200]}")
            try:
                return response.json()
            except ValueError as error:
                raise SourceUnavailable("Jev answered with invalid JSON") from error
        raise SourceUnavailable("Jev retries exhausted")  # pragma: no cover


def _parse_response(body: Mapping[str, Any], questions: Mapping[str, Question]) -> tuple[dict[str, Answer], str]:
    try:
        version = str(body["model"])
        answers: dict[str, Answer] = {}
        for name, question in questions.items():
            raw = body["answers"][name]
            choice = raw["choice"]
            probabilities = {option: float(p) for option, p in raw["probabilities"].items()}
            if raw.get("type") != "choice" or choice not in question.criteria or choice not in probabilities:
                raise ValueError(f"malformed answer for {name}")
            answers[name] = Answer(choice, probabilities)
        return answers, version
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise SourceUnavailable(f"Jev response not usable: {error}") from error


def offline_jev(model: str = DEFAULT_MODEL) -> JevClient:
    """Recorded answers; an unrecorded request raises FixtureMissing. Never touches the network."""
    return JevClient(FixtureTransport(), model)


def live_jev(record: bool = False, model: str = DEFAULT_MODEL) -> JevClient:
    """The TypeSafe API. With record=True each response is also written to server/fixtures/."""
    return JevClient(RecordingTransport() if record else LiveTransport(), model)


# --- the cache --------------------------------------------------------------------------------------


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def cache_key(model: str, key: str, kind: str, evidence: Mapping[str, Any]) -> str:
    """Model, evidence and question, as ADR-0003 says. The question is its full wording."""
    return _digest({"model": model, "key": key, "kind": kind, "evidence": evidence,
                    "question": build_question(key, kind).wire()})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnswerCache:
    """SQLite answers by cache key, plus gate results by model version. Use ":memory:" in tests."""

    def __init__(self, path: Path | str = CACHE_PATH):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS answers (cache_key TEXT PRIMARY KEY, model TEXT NOT NULL,"
            " model_version TEXT NOT NULL, choice TEXT NOT NULL, probabilities TEXT NOT NULL, stored_at TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS gate_results (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL,"
            " model_version TEXT NOT NULL, passed INTEGER NOT NULL, detail TEXT NOT NULL, ran_at TEXT NOT NULL);"
        )

    def get(self, key: str) -> tuple[Answer, str] | None:
        """The cached answer and the model version that gave it."""
        row = self.db.execute(
            "SELECT choice, probabilities, model_version FROM answers WHERE cache_key = ?", (key,)
        ).fetchone()
        return (Answer(row[0], json.loads(row[1])), row[2]) if row else None

    def put(self, key: str, model: str, model_version: str, answer: Answer) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?, ?, ?)",
            (key, model, model_version, answer.choice, json.dumps(dict(answer.probabilities)), _now()),
        )
        self.db.commit()

    def record_gate(self, model: str, result: "GateResult") -> None:
        self.db.execute(
            "INSERT INTO gate_results (model, model_version, passed, detail, ran_at) VALUES (?, ?, ?, ?, ?)",
            (model, result.model_version or "", int(result.passed), json.dumps(result.summary()), _now()),
        )
        self.db.commit()

    def gate_passed(self, model_version: str) -> bool:
        """True iff the latest gate run for this model version passed. No run means no."""
        row = self.db.execute(
            "SELECT passed FROM gate_results WHERE model_version = ? ORDER BY id DESC LIMIT 1", (model_version,)
        ).fetchone()
        return bool(row and row[0])


# --- batching per route ------------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenCrossing:
    """A crossing with questions to ask. `evidence` is the crossing's records plus its approach
    bearing and kind, as a JSON-serializable dict; S3 decides its contents."""

    key: str
    evidence: Mapping[str, Any]
    kinds: tuple[str, ...] = tuple(KINDS)


@dataclass(frozen=True)
class BatchStats:
    crossings: int
    questions: int
    hits: int
    misses: int
    calls: int  # 0 when every question hit the cache, else 1
    latency_s: float  # wall time of the Jev call; 0.0 when none was made
    model_version: str | None  # the version that answered; the cached one on an all-hit batch


def ask_route(jev: Jev, cache: AnswerCache, crossings: Sequence[OpenCrossing]) -> tuple[dict[str, Answer], BatchStats]:
    """All open questions for one route: hits come from the cache, the misses go in one Jev request."""
    answers: dict[str, Answer] = {}
    missing: dict[str, tuple[Question, str, OpenCrossing]] = {}
    cached_version: str | None = None
    for crossing in crossings:
        for kind in crossing.kinds:
            name = question_name(crossing.key, kind)
            ck = cache_key(jev.model, crossing.key, kind, crossing.evidence)
            hit = cache.get(ck)
            if hit:
                answers[name], cached_version = hit
            else:
                missing[name] = (build_question(crossing.key, kind), ck, crossing)
    hits = len(answers)
    if not missing:
        return answers, BatchStats(len(crossings), hits, hits, 0, 0, 0.0, cached_version)
    state = {"crossings": {c.key: dict(c.evidence) for _, _, c in missing.values()}}
    started = time.perf_counter()
    fresh, version = jev.ask(state, {name: q for name, (q, _, _) in missing.items()})
    latency = time.perf_counter() - started
    for name, (_, ck, _) in missing.items():
        cache.put(ck, jev.model, version, fresh[name])
        answers[name] = fresh[name]
    stats = BatchStats(len(crossings), len(answers), hits, len(missing), 1, latency, version)
    log.info("jev batch crossings=%d questions=%d hits=%d misses=%d latency=%.2fs model=%s",
             stats.crossings, stats.questions, hits, len(missing), latency, version)
    return answers, stats


def ask_batches(jev: Jev, cache: AnswerCache, crossings: Sequence[OpenCrossing],
                size: int = MAX_CROSSINGS_PER_REQUEST) -> tuple[dict[str, Answer], list[BatchStats]]:
    """For a corridor rather than a route: ask_route in chunks small enough for one request each."""
    answers: dict[str, Answer] = {}
    stats: list[BatchStats] = []
    for start in range(0, len(crossings), size):
        got, batch = ask_route(jev, cache, crossings[start:start + size])
        answers.update(got)
        stats.append(batch)
    return answers, stats


# --- the gate -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SettledCrossing:
    """What S3 hands the gate: a crossing the rules settled from clear records, with the rule's
    answer for each question it settled (kind -> option, using the options in KINDS)."""

    key: str
    evidence: Mapping[str, Any]
    rule_answers: Mapping[str, str]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    model_version: str | None
    settled: int  # crossings
    compared: int  # questions Jev answered at the threshold with a definite option
    agree: int
    disagree: int
    abstain: int  # below the threshold, or an "I cannot tell" option
    disagreements: tuple[tuple[str, str, str, float], ...] = ()  # (question, rule, jev, probability)
    batches: tuple[BatchStats, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {"passed": self.passed, "settled": self.settled, "compared": self.compared, "agree": self.agree,
                "disagree": self.disagree, "abstain": self.abstain,
                "disagreements": [list(d) for d in self.disagreements]}

    def line(self) -> str:
        return (f"jev gate {'PASS' if self.passed else 'FAIL'} model={self.model_version} settled={self.settled} "
                f"compared={self.compared} agree={self.agree} disagree={self.disagree} abstain={self.abstain}")


def evaluate_gate(rule_answers: Mapping[str, str], answers: Mapping[str, Answer], model_version: str | None,
                  settled: int = 0, min_agree: int = 1, threshold: float = THRESHOLD) -> GateResult:
    """Pure arithmetic. `rule_answers` and `answers` are keyed by question name ("{key}.{kind}").

    A question is compared only when Jev's answer reaches the threshold and is not an
    "I cannot tell" option; otherwise it abstains. The gate passes iff Jev never confidently
    disagrees AND agrees at least `min_agree` times (an empty comparison is not a pass).
    """
    agree = disagree = abstain = 0
    disagreements: list[tuple[str, str, str, float]] = []
    for name, rule in rule_answers.items():
        answer = answers[name]
        value = counted(answer, threshold)
        if value is None or value in KINDS[split_name(name)[1]].abstains:
            abstain += 1
        elif value == rule:
            agree += 1
        else:
            disagree += 1
            disagreements.append((name, rule, value, answer.probability))
    return GateResult(disagree == 0 and agree >= min_agree, model_version, settled, agree + disagree, agree,
                      disagree, abstain, tuple(disagreements))


def run_gate(jev: Jev, cache: AnswerCache, settled: Sequence[SettledCrossing], min_agree: int = MIN_AGREE) -> GateResult:
    """Ask Jev the rule-settled crossings' questions, compare, store the result, log one line."""
    asked = [OpenCrossing(s.key, s.evidence, tuple(s.rule_answers)) for s in settled]
    answers, stats = ask_batches(jev, cache, asked)
    rules = {question_name(s.key, kind): value for s in settled for kind, value in s.rule_answers.items()}
    version = next((b.model_version for b in stats if b.model_version), None)
    result = replace(evaluate_gate(rules, answers, version, len(settled), min_agree), batches=tuple(stats))
    cache.record_gate(jev.model, result)
    log.info(result.line())
    return result


# --- the advisor assessment asks (the S3 seam) -------------------------------------------------------


class JevAdvisor:
    """Serves gapcross.assess.Advisor: cache hits first, the misses in one Jev request per route.
    The gate result is looked up for the model version that answered."""

    def __init__(self, jev: Jev, cache: AnswerCache):
        self.jev = jev
        self.cache = cache

    def advise(self, state: dict[str, Any], questions: Sequence[str]):
        from .assess import Advice, JevAnswer

        kinds: dict[str, list[str]] = {}
        for name in questions:
            key, kind = split_name(name)
            kinds.setdefault(key, []).append(kind)
        open_ = [OpenCrossing(key, state[key], tuple(ks)) for key, ks in kinds.items()]
        answers, stats = ask_route(self.jev, self.cache, open_)
        version = stats.model_version or self.jev.model
        return Advice(
            answers={n: JevAnswer(a.choice, a.probability) for n, a in answers.items()},
            model_version=version,
            gate_passed=self.cache.gate_passed(version),
        )
