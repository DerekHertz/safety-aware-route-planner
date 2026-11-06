"""Flow B: corridor pre-run and Jev gate (offline). See docs/design/properties.md.

    uv run python -m gapcross.prerun [--record] [--offline] [--cache FILE] [--corridor FILE]

1. enumerate the corridor's crossings (`corridor`: provider, default and candidate routes of every
   trip, on recorded sources) and split them with the rules: settled set S, open set O
2. gate: ask Jev S's questions, compare with the rules' answers, store pass/fail with the model version
3. ask O's questions and write the answers to the cache (they stay leads unless the gate passed)
4. one log line with the counts and pass or fail

`--corridor FILE` instead reads a hand-made split of this shape:

    {"settled": [{"key": "...", "evidence": {...}, "rule_answers": {"approach_control": "stops_for_us"}}],
     "open":    [{"key": "...", "evidence": {...}, "kinds": ["approach_control", "road_function"]}]}
"""

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .assess import FixedAdvisor, assess_route, rule_verdicts
from .compare import CANDIDATE_LIMIT
from .interpret import interpret
from .search import targets
from .sources import Sources, offline_sources
from .trips import TripPlan, load_trips

from .jev import (
    AnswerCache,
    BatchStats,
    GateResult,
    Jev,
    KINDS,
    MIN_AGREE,
    OpenCrossing,
    SettledCrossing,
    ask_batches,
    live_jev,
    offline_jev,
    run_gate,
)

log = logging.getLogger("gapcross.jev")


@dataclass(frozen=True)
class PreRunResult:
    gate: GateResult
    open_crossings: int
    open_batches: tuple[BatchStats, ...]

    def line(self) -> str:
        misses = sum(b.misses for b in self.open_batches)
        hits = sum(b.hits for b in self.open_batches)
        calls = sum(b.calls for b in self.open_batches)
        return f"{self.gate.line()} open={self.open_crossings} open_hits={hits} open_misses={misses} open_calls={calls}"


def prerun(jev: Jev, cache: AnswerCache, settled: Sequence[SettledCrossing], open_: Sequence[OpenCrossing],
           min_agree: int = MIN_AGREE) -> PreRunResult:
    gate = run_gate(jev, cache, settled, min_agree)
    _, batches = ask_batches(jev, cache, open_)
    result = PreRunResult(gate, len(open_), tuple(batches))
    log.info(result.line())
    return result


RULE_ANSWER = {"flagged": "does_not_stop", "unflagged": "stops_for_us"}


def corridor(sources: Sources, trips: Mapping[str, TripPlan], limit: int = CANDIDATE_LIMIT
             ) -> tuple[list[SettledCrossing], list[OpenCrossing]]:
    """Every crossing the service can meet in each trip's corridor: on the provider route, the
    default route and every candidate the search would try. The rules split them: settled ones
    (flagged or unflagged from records) go to the gate with the rule's approach_control answer,
    open ones get their question asked. Evidence is built exactly as the service builds it, so the
    answers land under the cache keys the service will look up."""
    settled: dict[str, SettledCrossing] = {}
    open_: dict[str, OpenCrossing] = {}
    for trip in trips.values():
        snapshot = sources.records.snapshot(trip.bbox)
        default = sources.routes.route([trip.origin, *trip.example_via, trip.destination])
        routes = [sources.routes.route([trip.origin, trip.destination]), default]
        d = assess_route(default, _Held(snapshot), trip.bbox, FixedAdvisor())
        if d.interpretation is not None:
            routes += [sources.routes.route(list(t.waypoints))
                       for t in targets(trip, default, d.interpretation, d.crossings, snapshot, limit)]
        for raw in routes:
            for v in rule_verdicts(interpret(raw, snapshot), snapshot):
                key = v.crossing.key
                if key in settled or key in open_:
                    continue
                if v.status in RULE_ANSWER:
                    settled[key] = SettledCrossing(key, v.state, {"approach_control": RULE_ANSWER[v.status]})
                elif v.open_question:
                    open_[key] = OpenCrossing(key, v.state, ("approach_control",))
    return list(settled.values()), list(open_.values())


class _Held:
    def __init__(self, snapshot):
        self.snap = snapshot

    def snapshot(self, bbox):
        return self.snap


def load_corridor(path: Path | None, sources: Sources | None = None) -> tuple[list[SettledCrossing], list[OpenCrossing]]:
    if path is None:
        return corridor(sources or offline_sources(), load_trips())
    data = json.loads(path.read_text())
    settled = [SettledCrossing(c["key"], c["evidence"], c["rule_answers"]) for c in data["settled"]]
    open_ = [OpenCrossing(c["key"], c["evidence"], tuple(c.get("kinds", KINDS))) for c in data["open"]]
    for c in settled:
        if not set(c.rule_answers) <= set(KINDS):
            raise SystemExit(f"{c.key}: unknown question kind in rule_answers")
    return settled, open_


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corridor", type=Path)
    parser.add_argument("--cache", type=Path, default=None, help="default: server/var/jev_cache.sqlite")
    parser.add_argument("--offline", action="store_true", help="recorded answers only")
    parser.add_argument("--record", action="store_true", help="also record live answers to server/fixtures/")
    parser.add_argument("--min-agree", type=int, default=MIN_AGREE)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settled, open_ = load_corridor(args.corridor)
    jev = offline_jev() if args.offline else live_jev(record=args.record)
    cache = AnswerCache(args.cache) if args.cache else AnswerCache()
    result = prerun(jev, cache, settled, open_, args.min_agree)
    raise SystemExit(0 if result.gate.passed else 1)


if __name__ == "__main__":
    main()
