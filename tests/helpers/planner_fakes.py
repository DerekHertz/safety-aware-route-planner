"""Hand-computable fakes for the Trip planner: a scripted Jev and a `/route`."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from planner.jev import JevError, JevReply, Questions


def route(kind: str, eta_s: float, lefts: int, crossings: int, fastest: float = 600.0,
          wait_s: float = 60.0) -> dict[str, Any]:
    return {"kind": kind, "eta_s": eta_s, "distance_m": 1000.0,
            "detour_pct": (eta_s - fastest) / fastest,
            "unsafe": {"unprotected_left": lefts, "uncontrolled_crossing": crossings,
                       "total": lefts + crossings},
            "control_delay_s": wait_s, "geometry": {}, "segments": [],
            "unsafe_points": [], "maneuvers": [], "preference": {}, "schema_version": 2}


# fast 10 min with 2 lefts + 1 crossing; balanced 11 min, 1 crossing; safe 13 min, clean.
THREE = [route("fast", 600, 2, 1), route("balanced", 660, 0, 1), route("safe", 780, 0, 0)]


class FakeRoutes:
    def __init__(self, routes: list[dict[str, Any]] | None = None,
                 error: Exception | None = None) -> None:
        self.routes = routes if routes is not None else THREE
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def route(self, body: dict[str, Any],
                    forwarded_for: str | None = None) -> list[dict[str, Any]]:
        self.calls.append(body)
        if self.error:
            raise self.error
        return self.routes


def parse_answers(lefts: float = 0.0, crossings: float = 0.0, all_lefts: float = 0.0,
                  tradeoff: int = 2, detour: str = "none", departure: str = "now",
                  beyond: float = 0.0, clause_yes: frozenset[int] = frozenset()
                  ) -> Callable[[Any, Questions], dict[str, Any]]:
    """Answers for a parse request: every question gets a well-formed answer."""
    def answer(state: Any, questions: Questions) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in questions:
            if name.startswith("clause_"):
                out[name] = {"type": "noul",
                             "noul": 0.9 if int(name[7:]) in clause_yes else 0.05}
        nouls = {"no_unprotected_lefts": lefts, "no_uncontrolled_crossings": crossings,
                 "all_lefts": all_lefts, "beyond": beyond}
        out.update({k: {"type": "noul", "noul": v} for k, v in nouls.items()})
        probs = {str(i): (0.8 if i == tradeoff + 2 else 0.05) for i in range(5)}
        out["tradeoff"] = {"type": "score", "score": tradeoff + 2, "confidence": 0.7,
                           "probabilities": probs, "legend": {}}
        out["detour"] = {"type": "choice", "choice": detour, "confidence": 0.9,
                         "probabilities": {detour: 0.9}}
        out["departure"] = {"type": "choice", "choice": departure, "confidence": 0.9,
                            "probabilities": {departure: 0.9}}
        return out
    return answer


class ScriptedJev:
    """Answers the parse with `parse`, and the Choice with `choice` (a kind)."""

    def __init__(self, parse: Callable[[Any, Questions], dict[str, Any]],
                 choice: str | None = "safe", fail: bool = False) -> None:
        self.parse = parse
        self.choice = choice
        self.fail = fail
        self.calls: list[tuple[Any, Questions]] = []

    async def ask(self, state: Any, questions: Questions) -> JevReply:
        self.calls.append((state, questions))
        if self.fail:
            raise JevError("scripted failure")
        if "choice" in questions:
            if self.choice is None:
                raise JevError("scripted choice failure")
            answers = {"choice": {"type": "choice", "choice": self.choice,
                                  "confidence": 0.8, "probabilities": {self.choice: 0.85}}}
        else:
            answers = self.parse(state, questions)
        return JevReply(answers=answers, model="jev-1.14.0", input_tokens=1000,
                        latency_ms=100.0)
