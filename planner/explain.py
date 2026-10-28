"""The Explanation: a code template, grounded by construction (ADR-0019,
2026-10-27 amendment).

Every number and route name below is copied from a `Summary` or from the
Parse; nothing is generated. `tests/test_planner_explain.py` checks that
property directly: every number in an Explanation appears in its inputs.
"""
from __future__ import annotations

import datetime
from collections.abc import Sequence

from planner.choose import DETOUR, NO_CROSSINGS, NO_LEFTS, RelaxationProposal, Summary
from planner.parse import DETOUR_BUCKETS, Parse


def _n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _minutes(n: int) -> str:
    return _n(n, "min", "min")


def _risk(s: Summary) -> str:
    return (f"{_n(s.unprotected_lefts, 'unprotected left', 'unprotected lefts')} and "
            f"{_n(s.uncontrolled_crossings, 'uncontrolled crossing', 'uncontrolled crossings')}")


def detour_phrase(bucket: str) -> str | None:
    spec = DETOUR_BUCKETS.get(bucket)
    if spec is None:
        return None
    unit, value = spec
    if unit == "min":
        return f"at most {int(value)} min longer than the fastest route"
    return f"at most {round(value * 100)}% longer than the fastest route"


CONSTRAINT_LABELS = {
    NO_LEFTS: "no unprotected left turns",
    NO_CROSSINGS: "no uncontrolled crossings",
    DETOUR: "the detour limit",
}

_TRADEOFF = {
    2: "You put safety first.",
    1: "You leaned toward safety.",
    -1: "You leaned toward speed.",
    -2: "You put speed first.",
}

_DEPARTURE = {
    "weekday_am_peak": "a weekday morning-peak departure",
    "weekday_pm_peak": "a weekday evening-peak departure",
    "off_peak": "a midday off-peak departure",
    "night": "a night-time departure",
}


def _chosen_sentence(chosen: Summary, summaries: Sequence[Summary]) -> str:
    fastest = min(summaries, key=lambda s: s.eta_s)
    head = f"Chose {chosen.kind}: {_minutes(chosen.minutes)}"
    if chosen.kind == fastest.kind or chosen.extra_minutes <= 0:
        head += ", the fastest"
    else:
        head += f", {_minutes(chosen.extra_minutes)} longer than {fastest.kind}"
    head += f", with {_risk(chosen)}"
    others = [s for s in summaries if s.kind != chosen.kind]
    if others:
        head += " (" + "; ".join(f"{s.kind}: {_risk(s)}" for s in others) + ")"
    return head + "."


def constraint_sentences(parse: Parse, relaxed: Sequence[str]) -> list[str]:
    kept = []
    if parse.no_unprotected_lefts and NO_LEFTS not in relaxed:
        kept.append(CONSTRAINT_LABELS[NO_LEFTS])
    if parse.no_uncontrolled_crossings and NO_CROSSINGS not in relaxed:
        kept.append(CONSTRAINT_LABELS[NO_CROSSINGS])
    detour = detour_phrase(parse.detour)
    if detour and DETOUR not in relaxed:
        kept.append(detour)
    out = []
    if kept:
        out.append("Kept to your limits: " + "; ".join(kept) + ".")
    if relaxed:
        out.append("As you confirmed, treated as a preference rather than a rule: "
                   + "; ".join(CONSTRAINT_LABELS.get(r, r) for r in relaxed) + ".")
    if parse.narrowed_all_lefts and parse.no_unprotected_lefts:
        out.append("Read \"no left turns\" as no unprotected left turns; other left "
                   "turns may remain.")
    return out


def unsupported_sentences(parse: Parse,
                          departure_at: datetime.datetime | None) -> list[str]:
    out = []
    if departure_at is not None and parse.departure in _DEPARTURE:
        out.append(f"Planned for {_DEPARTURE[parse.departure]} "
                   f"({departure_at:%a %H:%M}).")
    if parse.unsupported:
        out.append("Could not apply: " + "; ".join(f"\"{c}\"" for c in parse.unsupported)
                   + ".")
    elif parse.beyond:
        out.append("Part of your request asks for something the planner cannot express "
                   "yet, so it was not applied.")
    if parse.detour == "other":
        out.append("Your time limit could not be expressed, so no limit was applied.")
    if parse.departure == "other":
        out.append("Your departure time could not be expressed, so this plans for now.")
    return out


def explain_choice(parse: Parse, chosen: Summary, summaries: Sequence[Summary],
                   cands: Sequence[Summary], relaxed: Sequence[str],
                   departure_at: datetime.datetime | None) -> list[str]:
    out = [_chosen_sentence(chosen, summaries)]
    if parse.tradeoff in _TRADEOFF:
        out.append(_TRADEOFF[parse.tradeoff])
    out += constraint_sentences(parse, relaxed)
    ruled_out = [s.kind for s in summaries if s.kind not in {c.kind for c in cands}]
    if ruled_out:
        out.append("Ruled out by your limits: " + ", ".join(ruled_out) + ".")
    out += unsupported_sentences(parse, departure_at)
    return out


def explain_infeasible(parse: Parse, proposal: RelaxationProposal,
                       relaxed: Sequence[str],
                       departure_at: datetime.datetime | None) -> list[str]:
    names = [CONSTRAINT_LABELS.get(c, c) for c in proposal.constraints]
    out = ["No route meets all of your limits."]
    if proposal.would_allow:
        out.append(f"Treating {' and '.join(names)} as a preference instead of a rule "
                   f"would allow: {', '.join(proposal.would_allow)}. Confirm to replan.")
    out += [s for s in constraint_sentences(parse, relaxed) if s.startswith("As you")]
    out += unsupported_sentences(parse, departure_at)
    return out


UNAVAILABLE = ["The Trip planner is unavailable right now, so here is the plain "
               "comparison with no Choice."]
