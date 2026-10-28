"""Request -> Parse: one Jev request, every question answered in parallel.

Jev judges; code owns numbers and policy (ADR-0019, 2026-10-27 amendment).
Each question is self-contained (question names are not sent to the model),
written to be read literally, and has an explicit no-match answer, because
"nothing was asked" is the most common case for each of them.

The Unsupported clauses are *selected*, not written: code splits the Request
into clauses and asks one Noul per clause, so an Explanation quotes the user's
own words and never paraphrases them.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any

from planner.jev import Jev, JevReply, Questions

YES = 0.5          # a Noul at or above this is a yes
MAX_CLAUSES = 8

# --- detour budget ------------------------------------------------------------

# bucket -> ("min", minutes) | ("pct", fraction) | None (no budget stated, or
# one these buckets cannot express: "other" is an Unsupported clause).
DETOUR_BUCKETS: dict[str, tuple[str, float] | None] = {
    "none": None,
    "min_5": ("min", 5), "min_10": ("min", 10), "min_15": ("min", 15),
    "min_20": ("min", 20), "min_30": ("min", 30),
    "pct_10": ("pct", 0.10), "pct_20": ("pct", 0.20), "pct_30": ("pct", 0.30),
    "pct_50": ("pct", 0.50),
    "other": None,
}

_DETOUR_CRITERIA = {
    "none": "The request sets no limit on how much longer the trip may take. Vague "
            "wishes such as 'a bit longer is fine' or 'I'm not in a hurry' are this "
            "option.",
    "min_5": "A limit of about 5 extra minutes (anything from 1 to 7 minutes).",
    "min_10": "A limit of about 10 extra minutes (8 to 12 minutes).",
    "min_15": "A limit of about 15 extra minutes (13 to 17 minutes, or a quarter hour).",
    "min_20": "A limit of about 20 extra minutes (18 to 25 minutes).",
    "min_30": "A limit of about 30 extra minutes (26 to 40 minutes, or half an hour).",
    "pct_10": "A limit of about 10 percent longer than the fastest route (5 to 14 percent).",
    "pct_20": "A limit of about 20 percent longer (15 to 24 percent, or a fifth).",
    "pct_30": "A limit of about 30 percent longer (25 to 39 percent, or a quarter to "
              "a third).",
    "pct_50": "A limit of about 50 percent longer (40 to 60 percent, or half again).",
    "other": "A limit on extra time stated in a way none of the other options fit, "
             "such as more than 40 minutes, an hour, more than 60 percent, or twice "
             "as long.",
}

# --- departure ----------------------------------------------------------------

DEPARTURE_BUCKETS = ("now", "weekday_am_peak", "weekday_pm_peak", "off_peak", "night",
                     "other")
# bucket -> (hour, minute, weekdays only). Wall-clock in the pack's timezone,
# which is how the route service reads a naive departure_time.
DEPARTURE_TIMES: dict[str, tuple[int, int, bool]] = {
    "weekday_am_peak": (8, 0, True),
    "weekday_pm_peak": (17, 30, True),
    "off_peak": (13, 0, False),
    "night": (2, 0, False),
}

_DEPARTURE_CRITERIA = {
    "now": "No departure time is mentioned, or the traveller is leaving now, right "
           "away, or as soon as possible.",
    "weekday_am_peak": "Leaving in the weekday morning rush or commute: roughly 7 to "
                       "9:30 am, Monday to Friday, or just 'morning rush hour'.",
    "weekday_pm_peak": "Leaving in the weekday evening rush or commute: roughly 4 to "
                       "7 pm, Monday to Friday, or just 'after work' or 'evening "
                       "rush hour'.",
    "off_peak": "Leaving in the middle of the day outside rush hour, roughly 10 am to "
                "3 pm, such as 'at lunchtime' or 'midday'.",
    "night": "Leaving late at night or before dawn, roughly 10 pm to 5 am.",
    "other": "A departure time that none of the other options fit, such as a weekend "
             "day, a specific date, or an early evening after 7 pm.",
}

# --- the Tradeoff -------------------------------------------------------------

TRADEOFF_LEVELS = [
    "Speed matters far more than safety: the request asks for the fastest or quickest "
    "route, or says the traveller is in a hurry or late, and does not ask for safety.",
    "Leans toward speed: the request prefers a quicker route but does not insist on "
    "the fastest.",
    "No lean either way: the request says nothing about safety versus speed, or asks "
    "for a balance between them.",
    "Leans toward safety: the request prefers a safer or calmer route, or fewer risky "
    "turns or crossings where possible, but accepts some extra risk or time either way.",
    "Safety matters far more than speed: the request asks for the safest route, or "
    "says safety comes first even if the trip takes longer.",
]

SUPPORTED_TOPICS = (
    "avoiding left turns or unprotected left turns; avoiding crossing busy roads "
    "without a traffic light or stop sign; preferring a safer or a faster route; a "
    "limit on how much longer the trip may take; when the traveller leaves"
)


@dataclass(frozen=True)
class Parse:
    no_unprotected_lefts: bool
    no_uncontrolled_crossings: bool
    narrowed_all_lefts: bool        # asked to avoid all lefts; read as unprotected
    tradeoff: int                   # -2 (speed) .. +2 (safety)
    detour: str                     # a DETOUR_BUCKETS key
    departure: str                  # a DEPARTURE_BUCKETS entry
    unsupported: tuple[str, ...]    # the user's own clauses, verbatim
    beyond: bool                    # the whole-Request Noul: something unsupported

    @property
    def has_unsupported(self) -> bool:
        return bool(self.unsupported) or self.beyond or self.detour == "other" \
            or self.departure == "other"

    def labels(self) -> dict[str, Any]:
        """The fields graded by exact match in `evals/requests.jsonl`."""
        return {
            "no_unprotected_lefts": self.no_unprotected_lefts,
            "no_uncontrolled_crossings": self.no_uncontrolled_crossings,
            "tradeoff": self.tradeoff,
            "detour": self.detour,
            "departure": self.departure,
            "unsupported": self.has_unsupported,
        }


_SPLIT = re.compile(r"[,;:.!?()]+|\s+-+\s+|\b(?:and|but|also|though|although|while|"
                    r"plus|unless|except)\b", re.IGNORECASE)


def split_clauses(text: str) -> list[str]:
    parts = [p.strip(" '\"") for p in _SPLIT.split(text)]
    clauses = [p for p in parts if len(p) >= 3]
    return clauses[:MAX_CLAUSES]


def parse_state(text: str) -> dict[str, Any]:
    return {"request": text, "clauses": split_clauses(text)}


def parse_questions(state: dict[str, Any]) -> Questions:
    q: dict[str, dict[str, Any]] = {
        "no_unprotected_lefts": {
            "type": "noul",
            "instructions": "Does the traveller's `request` firmly rule out unprotected "
                            "left turns (left turns across oncoming traffic without a "
                            "green arrow), or all left turns?",
            "criteria": {
                "true": "A firm rule: 'no left turns', 'no unprotected lefts', 'avoid "
                        "left turns without an arrow', 'I can't do lefts across "
                        "traffic', 'never turn left'.",
                "false": "No mention of left turns, or only a soft wish such as "
                         "'fewer lefts if possible', 'prefer not to turn left', or "
                         "'lefts are fine'.",
            },
        },
        "no_uncontrolled_crossings": {
            "type": "noul",
            "instructions": "Does the traveller's `request` firmly rule out driving "
                            "straight across a busy road at an intersection that has no "
                            "traffic light or stop sign?",
            "criteria": {
                "true": "A firm rule: 'no uncontrolled crossings', 'only cross busy "
                        "roads at lights', 'don't make me cross a main road without a "
                        "signal', 'avoid unsignalized crossings'.",
                "false": "No mention of crossing busy roads, or only a soft wish such "
                         "as 'fewer risky crossings if possible'.",
            },
        },
        "all_lefts": {
            "type": "noul",
            "instructions": "Does the traveller's `request` ask to avoid every left "
                            "turn, without limiting it to unprotected or unsignalled "
                            "left turns?",
            "criteria": {
                "true": "'no left turns', 'avoid left turns', 'never turn left'.",
                "false": "No mention of left turns, or the request limits it to "
                         "unprotected, unsignalled or no-arrow left turns.",
            },
        },
        "tradeoff": {
            "type": "score",
            "instructions": "How much does the traveller's `request` weigh safety "
                            "against speed?",
            "criteria": TRADEOFF_LEVELS,
        },
        "detour": {
            "type": "choice",
            "instructions": "What limit, if any, does the traveller's `request` put on "
                            "how much longer than the fastest route the trip may take?",
            "criteria": _DETOUR_CRITERIA,
        },
        "departure": {
            "type": "choice",
            "instructions": "When does the traveller's `request` say they will leave?",
            "criteria": _DEPARTURE_CRITERIA,
        },
        "beyond": {
            "type": "noul",
            "instructions": "Does the traveller's `request` ask for anything other than "
                            f"these topics: {SUPPORTED_TOPICS}?",
            "criteria": {
                "true": "It also asks for something else, such as avoiding highways, "
                        "tolls or a named street, a stop on the way, scenery, parking, "
                        "a lane choice, or a different way to travel.",
                "false": "Every part of it is about the listed topics, or it is empty "
                         "of wishes.",
            },
        },
    }
    for i in range(len(state["clauses"])):
        q[f"clause_{i}"] = {
            "type": "noul",
            "instructions": f"Does `clauses[{i}]` ask for something other than these "
                            f"topics: {SUPPORTED_TOPICS}?",
            "criteria": {
                "true": "It asks for something outside the listed topics, such as "
                        "avoiding highways, tolls or a named street, a stop on the "
                        "way, scenery, parking, or a different way to travel.",
                "false": "It is about one of the listed topics, or it is filler with "
                         "no wish in it (such as 'please' or 'take me home').",
            },
        }
    return q


def interpret(state: dict[str, Any], reply: JevReply) -> Parse:
    detour, _ = reply.choice("detour")
    departure, _ = reply.choice("departure")
    if detour not in DETOUR_BUCKETS:
        detour = "other"
    if departure not in DEPARTURE_BUCKETS:
        departure = "other"
    unsupported = tuple(c for i, c in enumerate(state["clauses"])
                        if reply.noul(f"clause_{i}") >= YES)
    return Parse(
        no_unprotected_lefts=reply.noul("no_unprotected_lefts") >= YES,
        no_uncontrolled_crossings=reply.noul("no_uncontrolled_crossings") >= YES,
        narrowed_all_lefts=reply.noul("all_lefts") >= YES,
        tradeoff=reply.score_level("tradeoff") - 2,
        detour=detour,
        departure=departure,
        unsupported=unsupported,
        beyond=reply.noul("beyond") >= YES,
    )


async def parse_request(jev: Jev, text: str) -> tuple[Parse, JevReply]:
    state = parse_state(text)
    reply = await jev.ask(state, parse_questions(state))
    return interpret(state, reply), reply


def departure_time(bucket: str, now_local: datetime.datetime) -> datetime.datetime | None:
    """The next wall-clock instant for a bucket, from the client's local clock.
    None means "now" (the route service's own default). Date arithmetic lives
    here because Jev reads dates as text."""
    spec = DEPARTURE_TIMES.get(bucket)
    if spec is None:
        return None
    hour, minute, weekdays_only = spec
    day = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day <= now_local:
        day += datetime.timedelta(days=1)
    while weekdays_only and day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day
