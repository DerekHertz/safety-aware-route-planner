"""The Trip planner eval (ADR-0019 "Measurement").

    python -m planner.eval              # replay recorded Jev replies: free, deterministic (CI)
    python -m planner.eval --live       # call Jev, re-record, report latency and cost

Each row of `evals/requests.jsonl` runs through the real pipeline
(`planner.pipeline.plan`), with `/route` answered by the frozen route set in
`evals/scenarios.json` that the row names and the clock frozen at `NOW`.
Reported:

- **parse exact match**: all six graded fields right, plus each field alone,
  overall and on the held-out split;
- **Choice-vs-rule agreement**: how often Jev's Choice equals the frozen rule
  baseline's pick over the same Candidates (owner picks come in PR B);
- live only: **latency** of the Jev layer and of the whole plan, and **cost**.

A replay miss (a prompt or state changed since recording) counts as a Jev
failure and is reported; re-record with `--live`, which needs
`TYPESAFE_API_KEY`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from planner.jev import HttpJev, Jev, RecordingJev, ReplayJev
from planner.pipeline import plan
from planner.schemas import PlanRequest, TripParse

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
RECORDING = EVALS / "recordings" / "jev.jsonl"
NOW = "2026-10-27T09:30"        # a Tuesday morning; departure buckets resolve from it
FIELDS = ("no_unprotected_lefts", "no_uncontrolled_crossings", "tradeoff", "detour",
          "departure", "unsupported")
ORIGIN = {"lat": 37.8044, "lon": -122.2712}
DESTINATION = {"lat": 37.8716, "lon": -122.2727}


class ScenarioRoutes:
    def __init__(self, routes: list[dict[str, Any]]) -> None:
        self.routes = routes

    async def route(self, body: dict[str, Any],
                    forwarded_for: str | None = None) -> list[dict[str, Any]]:
        return self.routes


@dataclass
class RowResult:
    id: str
    split: str
    parsed: dict[str, Any] | None
    expect: dict[str, Any]
    status: str
    choice: str | None
    choice_source: str | None
    rule: str | None
    jev_ms: float
    total_ms: float
    tokens: int
    cost: float

    @property
    def exact(self) -> bool:
        return self.parsed is not None and all(self.parsed[f] == self.expect[f]
                                               for f in FIELDS)


def load_rows(path: Path = EVALS / "requests.jsonl") -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def load_scenarios(path: Path = EVALS / "scenarios.json") -> dict[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def labels(p: TripParse | None) -> dict[str, Any] | None:
    """The graded fields of a wire parse; `Parse.labels()` read off the wire."""
    if p is None:
        return None
    return {"no_unprotected_lefts": p.no_unprotected_lefts,
            "no_uncontrolled_crossings": p.no_uncontrolled_crossings,
            "tradeoff": p.tradeoff, "detour": p.detour, "departure": p.departure,
            "unsupported": p.has_unsupported}


async def run_row(jev: Jev, row: dict[str, Any],
                  scenarios: dict[str, list[dict[str, Any]]]) -> RowResult:
    req = PlanRequest(origin=ORIGIN, destination=DESTINATION,  # type: ignore[arg-type]
                      request=row["request"], now_local=NOW)
    resp, m = await plan(req, jev, ScenarioRoutes(scenarios[row["scenario"]]))
    return RowResult(row["id"], row.get("split", "tune"), labels(resp.parse), row["expect"],
                     resp.status, resp.choice, resp.choice_source, resp.rule_choice,
                     m.jev_ms, resp.timing.total_ms, m.tokens, m.cost)


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def report(results: Sequence[RowResult], live: bool) -> dict[str, Any]:
    parsed = [r for r in results if r.parsed is not None]
    holdout = [r for r in results if r.split == "holdout"]
    out: dict[str, Any] = {
        "rows": len(results),
        "jev_failures": len(results) - len(parsed),
        "parse_exact": sum(r.exact for r in results),
        "parse_exact_holdout": sum(r.exact for r in holdout),
        "holdout_rows": len(holdout),
        "fields": {f: sum(1 for r in parsed if r.parsed and r.parsed[f] == r.expect[f])
                   for f in FIELDS},
    }
    jev_choices = [r for r in results if r.choice_source == "jev"]
    out["jev_choices"] = len(jev_choices)
    out["choice_agrees_with_rule"] = sum(r.choice == r.rule for r in jev_choices)
    out["infeasible"] = sum(r.status == "infeasible" for r in results)
    if live and parsed:
        jev_ms = sorted(r.jev_ms for r in parsed)
        total_ms = sorted(r.total_ms for r in parsed)
        out["jev_ms_p50"] = round(statistics.median(jev_ms))
        out["jev_ms_p95"] = round(jev_ms[min(len(jev_ms) - 1, int(0.95 * len(jev_ms)))])
        out["total_ms_p50"] = round(statistics.median(total_ms))
        out["total_ms_p95"] = round(total_ms[min(len(total_ms) - 1,
                                                 int(0.95 * len(total_ms)))])
        out["input_tokens"] = sum(r.tokens for r in results)
        out["cost_usd"] = round(sum(r.cost for r in results), 6)
    return out


def render(out: dict[str, Any], results: Sequence[RowResult]) -> str:
    n = out["rows"]
    lines = [
        f"rows: {n}   Jev failures: {out['jev_failures']}",
        f"parse exact match: {_pct(out['parse_exact'], n)}   "
        f"held out: {_pct(out['parse_exact_holdout'], out['holdout_rows'])}",
        "  per field: " + ", ".join(f"{f} {_pct(c, n)}" for f, c in out["fields"].items()),
        f"Choice vs rule: {_pct(out['choice_agrees_with_rule'], out['jev_choices'])} agree "
        f"(over Jev Choices; {out['infeasible']} infeasible)",
    ]
    if "jev_ms_p50" in out:
        lines.append(f"latency: Jev p50 {out['jev_ms_p50']} ms / p95 {out['jev_ms_p95']} ms; "
                     f"plan p50 {out['total_ms_p50']} ms / p95 {out['total_ms_p95']} ms")
        lines.append(f"cost: {out['input_tokens']} input tokens, ${out['cost_usd']:.6f}")
    misses = [r for r in results if r.parsed is not None and not r.exact]
    for r in misses:
        assert r.parsed is not None
        wrong = {f: (r.parsed[f], r.expect[f]) for f in FIELDS if r.parsed[f] != r.expect[f]}
        lines.append(f"  miss {r.id}: " + ", ".join(f"{f} got {g!r} want {w!r}"
                                                   for f, (g, w) in wrong.items()))
    return "\n".join(lines)


async def run(jev: Jev, rows: Sequence[dict[str, Any]]) -> list[RowResult]:
    scenarios = load_scenarios()
    # Sequential on purpose: live latency is per Trip, not under self-inflicted load.
    return [await run_row(jev, row, scenarios) for row in rows]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m planner.eval", description=__doc__.split(
        "\n\n")[0])
    ap.add_argument("--live", action="store_true",
                    help="call Jev (needs TYPESAFE_API_KEY) and re-record replies")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)
    # Failures are counted in the report; the per-row warnings are noise here.
    logging.getLogger("planner").setLevel(logging.ERROR)

    jev: Jev
    if args.live:
        live = HttpJev.from_env()
        if live is None:
            print("error: --live needs TYPESAFE_API_KEY", file=sys.stderr)
            return 2
        live.timeout_s = 10.0      # measure slow replies instead of failing them
        RECORDING.unlink(missing_ok=True)
        jev = RecordingJev(live, RECORDING)
    else:
        jev = ReplayJev.load(RECORDING)
    results = asyncio.run(run(jev, load_rows()))
    out = report(results, args.live)
    print(json.dumps(out, indent=2) if args.json else render(out, results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
