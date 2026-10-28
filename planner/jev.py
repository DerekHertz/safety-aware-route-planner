"""Jev, TypeSafe's System One model, over plain HTTP (ADR-0019, 2026-10-27 amendment).

One call is one `POST /v1/systemone`: a `state` and a dict of named questions,
answered in parallel. Jev returns typed judgments (Noul probability, Choice,
Score), never text, so every word the user reads is written by code.

Three implementations share one shape, so the pipeline never knows which it has:

- `HttpJev`: the live API, the key read from `TYPESAFE_API_KEY` on the server.
- `ReplayJev`: answers from recorded replies, keyed by a hash of the exact
  request. CI uses it, so evals are free and deterministic; a request that was
  never recorded is a `JevError`, which is the same path as a live outage.
- `RecordingJev`: wraps a live client and appends every reply to a recording.

No SDK: the wire format is four JSON fields, `httpx` is already a runtime
dependency, and recording at the HTTP boundary is what makes replay exact.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

API_URL = "https://api.typesafe.ai/v1/systemone"
# Pinned, not `jev-latest`: an alias moves when a release ships, which would
# change answers (and invalidate every recording) without a code change.
MODEL = "jev-1.14.0"
# models page, 2026-10-27: $0.042 per million input tokens; output is free.
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000

Questions = Mapping[str, Mapping[str, Any]]


class JevError(Exception):
    """Jev could not answer: network, timeout, HTTP error, bad reply, or (in
    replay) no recording. The pipeline treats every kind the same way."""


@dataclass(frozen=True)
class JevReply:
    answers: dict[str, dict[str, Any]]
    model: str
    input_tokens: int
    latency_ms: float

    @property
    def cost_usd(self) -> float:
        return self.input_tokens * USD_PER_INPUT_TOKEN

    def noul(self, name: str) -> float:
        return float(self._answer(name, "noul")["noul"])

    def choice(self, name: str) -> tuple[str, float]:
        a = self._answer(name, "choice")
        return str(a["choice"]), float(a["confidence"])

    def score_level(self, name: str) -> int:
        """The most probable level, not the expectation: Jev's levels are not
        numerically calibrated between each other (jev-1.13 jaggedness), so
        averaging them would invent precision."""
        probs = self._answer(name, "score")["probabilities"]
        return int(max(probs, key=lambda k: (probs[k], -int(k))))

    def _answer(self, name: str, kind: str) -> dict[str, Any]:
        a = self.answers.get(name)
        if not isinstance(a, dict) or a.get("type") != kind:
            raise JevError(f"reply has no {kind} answer for {name!r}")
        return a


class Jev(Protocol):
    async def ask(self, state: Any, questions: Questions) -> JevReply: ...


def request_body(state: Any, questions: Questions, model: str = MODEL) -> dict[str, Any]:
    return {"model": model, "state": state, "questions": dict(questions)}


def request_key(body: Mapping[str, Any]) -> str:
    """Identifies a request by content: any change to the state, a question's
    wording or the model is a different key, so a stale recording is a miss
    rather than a silently wrong answer."""
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _reply(data: Any, latency_ms: float) -> JevReply:
    try:
        return JevReply(answers=dict(data["answers"]), model=str(data["model"]),
                        input_tokens=int(data["usage"]["input_tokens"]),
                        latency_ms=latency_ms)
    except (KeyError, TypeError, ValueError) as exc:
        raise JevError(f"malformed reply: {exc}") from exc


class HttpJev:
    def __init__(self, api_key: str, timeout_s: float = 2.5, model: str = MODEL,
                 client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self._client = client or httpx.AsyncClient()

    @classmethod
    def from_env(cls) -> HttpJev | None:
        key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not key:
            return None
        return cls(key, timeout_s=float(os.environ.get("SR_PLANNER_JEV_TIMEOUT_S", "2.5")))

    async def ask(self, state: Any, questions: Questions) -> JevReply:
        body = request_body(state, questions, self.model)
        t0 = time.perf_counter()
        try:
            resp = await self._client.post(
                API_URL, json=body, timeout=self.timeout_s,
                headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as exc:
            raise JevError(f"{type(exc).__name__}") from exc
        latency_ms = (time.perf_counter() - t0) * 1000
        if resp.status_code != 200:
            # The body may echo the state (the Request); log the status only.
            raise JevError(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise JevError("reply is not JSON") from exc
        return _reply(data, latency_ms)

    async def aclose(self) -> None:
        await self._client.aclose()


class ReplayJev:
    """Answers from a recording: a JSONL file of `{"key", "reply"}` lines."""

    def __init__(self, replies: Mapping[str, Mapping[str, Any]], model: str = MODEL) -> None:
        self.replies = dict(replies)
        self.model = model
        self.misses: list[str] = []

    @classmethod
    def load(cls, path: Path) -> ReplayJev:
        replies: dict[str, Mapping[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    replies[row["key"]] = row["reply"]
        return cls(replies)

    async def ask(self, state: Any, questions: Questions) -> JevReply:
        key = request_key(request_body(state, questions, self.model))
        data = self.replies.get(key)
        if data is None:
            self.misses.append(key)
            raise JevError("no recorded reply for this request")
        return _reply(data, float(data.get("latency_ms", 0.0)))


class RecordingJev:
    """A live client that appends each reply to `path` for later replay."""

    def __init__(self, live: HttpJev, path: Path) -> None:
        self.live = live
        self.path = path

    async def ask(self, state: Any, questions: Questions) -> JevReply:
        reply = await self.live.ask(state, questions)
        key = request_key(request_body(state, questions, self.live.model))
        row = {"key": key, "reply": {
            "answers": reply.answers, "model": reply.model,
            "usage": {"input_tokens": reply.input_tokens, "output_tokens": 0},
            "latency_ms": round(reply.latency_ms, 1)}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return reply
