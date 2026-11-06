"""Assessment: each crossing's status from records first, then Jev for what the rules leave open.

Rules over OSM records, per carriageway junction of a crossing (abstract.md, CONTEXT.md):

- A signal at the junction or facing the driver's approach is unflagged, unless a stop or yield
  record also faces the driver (records conflict: unknown).
- An all-way stop (`stop=all`, or a stop facing every approach of the main street and the driver)
  is unflagged.
- A stop or yield facing the driver, with nothing recorded that stops the main street's approaching
  traffic, is flagged. A control on only some main-street approaches, or a yield, is unknown.
- No record facing the driver is unknown: the road-hierarchy convention is kept as a lead only.
- A control on the junction node itself without `stop=all` does not say which approach it faces:
  unknown.

A crossing is flagged if any carriageway is flagged, unflagged only if every carriageway is, and
otherwise unknown. Jev is asked once per route, only for crossings the rules leave unknown; an
answer counts only at probability >= 0.9 after the gate has passed, otherwise it is a lead.
A record source that fails, or a junction the snapshot does not cover, fails the whole check.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from .interpret import Interpretation, Movement, RouteCrossing, interpret
from .sources import BBox, OsmWay, RawRoute, RecordSnapshot, RecordSource, SourceUnavailable
from .types import Assessment, Crossing, Evidence, LatLon

REACH_M = 30.0  # how far up an approach a control record still applies to this junction
THRESHOLD = 0.9
SIGNAL, STOP, GIVE_WAY = "traffic_signals", "stop", "give_way"
CONTROLS = frozenset({SIGNAL, STOP, GIVE_WAY})

Status = Literal["flagged", "unflagged", "unknown", "check_failed"]


# --- The Jev seam, as assessment sees it -------------------------------------------------------

@dataclass(frozen=True)
class JevAnswer:
    choice: str
    probability: float


@dataclass(frozen=True)
class Advice:
    answers: Mapping[str, JevAnswer]
    model_version: str
    gate_passed: bool


class Advisor(Protocol):
    def advise(self, state: dict[str, Any], questions: Sequence[str]) -> Advice:
        """One batched request per route. Raises SourceUnavailable when Jev cannot answer."""
        ...


@dataclass(frozen=True)
class FixedAdvisor:
    """Fixture answers. With none given, every open question stays unknown."""

    advice: Advice = field(default_factory=lambda: Advice({}, "none", gate_passed=False))

    def advise(self, state: dict[str, Any], questions: Sequence[str]) -> Advice:
        return Advice({q: a for q, a in self.advice.answers.items() if q in questions},
                      self.advice.model_version, self.advice.gate_passed)


# --- Results ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteAssessment:
    status: Literal["assessed", "check_failed"]
    crossings: tuple[Crossing, ...]
    interpretation: Interpretation | None

    @property
    def flag_count(self) -> int | None:
        """Never shown for a failed check (rule 2)."""
        if self.status == "check_failed":
            return None
        return sum(c.assessment.status == "flagged" for c in self.crossings)

    @property
    def unknown_count(self) -> int | None:
        if self.status == "check_failed":
            return None
        return sum(c.assessment.status == "unknown" for c in self.crossings)


def assess_route(raw: RawRoute, records: RecordSource, bbox: BBox, advisor: Advisor) -> RouteAssessment:
    try:
        snapshot = records.snapshot(bbox)
    except SourceUnavailable:
        return RouteAssessment("check_failed", (), None)
    interpretation = interpret(raw, snapshot)
    rules = _Rules(snapshot)
    verdicts = {c.key: rules.crossing(c) for c in interpretation.crossings}

    open_keys = [k for k, v in verdicts.items() if v.status == "unknown" and v.ask]
    advice: Advice | None = None
    jev_failed = False
    if open_keys:
        questions = [f"{k}.approach_control" for k in open_keys]
        state = {k: verdicts[k].state for k in open_keys}
        try:
            advice = advisor.advise(state, questions)
        except SourceUnavailable:
            jev_failed = True

    crossings = []
    for c in interpretation.crossings:
        verdict = verdicts[c.key]
        if c.key in open_keys:
            verdict = _failed() if jev_failed else _apply_jev(c, verdict, advice, rules)
        crossings.append(_wire(c, verdict, rules.retrieved_at))

    failed = jev_failed or bool(interpretation.uncovered)
    return RouteAssessment("check_failed" if failed else "assessed", tuple(crossings), interpretation)


# --- Rules --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Fact:
    ref: str
    claim: str
    is_lead: bool = False
    source: Literal["osm", "jev"] = "osm"


@dataclass(frozen=True)
class _Verdict:
    status: Status
    explanation: str
    facts: tuple[_Fact, ...] = ()
    ask: bool = False  # Jev may settle it: no record faces the driver
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Arm:
    way: OsmWay
    bearing: float
    toward: tuple[int, ...]  # node ids walking away from the junction along this arm
    travel_forward: bool  # traffic heading to the junction on this arm moves in way order
    inbound: bool  # traffic can approach the junction on this arm
    origin: LatLon  # the junction
    junction: int


class _Rules:
    def __init__(self, snapshot: RecordSnapshot):
        self.snapshot = snapshot
        self.retrieved_at = snapshot.taken_at or datetime.now(UTC)
        self.ways_at: dict[int, list[tuple[OsmWay, int]]] = {}
        for way in snapshot.ways.values():
            for k, n in enumerate(way.node_ids):
                self.ways_at.setdefault(n, []).append((way, k))

    def crossing(self, c: RouteCrossing) -> _Verdict:
        parts = [self.carriageway(m, c.main_street) for m in c.movements]
        facts = tuple(f for p in parts for f in p.facts)
        state = jev_state(c, facts)
        flagged = [p for p in parts if p.status == "flagged"]
        if flagged:
            return _Verdict("flagged", flagged[0].explanation, facts, state=state)
        if all(p.status == "unflagged" for p in parts):
            return _Verdict("unflagged", parts[0].explanation, facts, state=state)
        first_open = next(p for p in parts if p.status != "unflagged")
        ask = any(p.ask for p in parts)
        return _Verdict("unknown", first_open.explanation, facts, ask, state)

    def carriageway(self, m: Movement, main: str) -> _Verdict:
        node = self.snapshot.nodes[m.node_id]
        tags = node.tags
        arms = self.arms(m.node_id)
        ours = _closest(arms, (m.approach_bearing + 180) % 360)
        mains = [a for a in arms if a is not ours and a.way.tags.get("name") == main and a.inbound]
        at_node = tags.get("highway")
        facts: list[_Fact] = []

        if at_node == SIGNAL:
            facts.append(_Fact(f"node/{m.node_id}", f"traffic signal at the {main} junction"))
        our_controls = self.facing(ours)
        facts += [_Fact(f"node/{n}", f"{_word(kind)} facing {m.from_street or 'the approach'}")
                  for n, kind in our_controls]
        our_kinds = {kind for _, kind in our_controls}

        if at_node == SIGNAL or SIGNAL in our_kinds:
            if our_kinds & {STOP, GIVE_WAY}:
                return _Verdict("unknown", f"Records at {main} show both a signal and a sign for you.", tuple(facts))
            return _Verdict("unflagged", f"A traffic signal controls the crossing of {main}.", tuple(facts))
        if at_node == STOP and tags.get("stop") == "all":
            facts.append(_Fact(f"node/{m.node_id}", f"all-way stop at the {main} junction"))
            return _Verdict("unflagged", f"An all-way stop controls the crossing of {main}.", tuple(facts))
        if at_node in (STOP, GIVE_WAY):
            facts.append(_Fact(f"node/{m.node_id}", f"{_word(at_node)} on the junction, approach not recorded"))
            return _Verdict("unknown", f"A {_word(at_node)} is recorded at {main} without the approach it faces.",
                            tuple(facts))

        main_controls = [(a, self.facing(a)) for a in mains]
        for _, controls in main_controls:
            facts += [_Fact(f"node/{n}", f"{_word(kind)} facing {main} traffic") for n, kind in controls]
        main_kinds = [{kind for _, kind in controls} for _, controls in main_controls]

        if our_kinds & {STOP, GIVE_WAY}:
            if not any(main_kinds):
                return _Verdict("flagged", _gap(m, main, our_kinds), tuple(facts))
            if STOP in our_kinds and mains and all(k == {STOP} for k in main_kinds):
                return _Verdict("unflagged", f"Stop signs face every approach at {main}.", tuple(facts))
            return _Verdict("unknown", f"Records at {main} disagree about who stops.", tuple(facts))

        facts.append(_Fact("convention", f"a local street usually stops for {main}", is_lead=True))
        return _Verdict("unknown", f"No traffic control is recorded for your approach to {main}.",
                        tuple(facts), ask=not any(main_kinds))

    def arms(self, node_id: int) -> list[_Arm]:
        here = self.snapshot.nodes[node_id].location
        result = []
        for way, k in self.ways_at.get(node_id, ()):
            oneway = _oneway(way)
            for step in (-1, 1):
                ids = way.node_ids[k + 1:] if step == 1 else way.node_ids[:k][::-1]
                ids = tuple(n for n in ids if n in self.snapshot.nodes)
                if not ids:
                    continue
                travel_forward = step == -1
                inbound = oneway == 0 or (oneway == 1) == travel_forward
                bearing = _bearing(here, self.snapshot.nodes[ids[0]].location)
                result.append(_Arm(way, bearing, ids, travel_forward, inbound, here, node_id))
        return result

    def facing(self, arm: _Arm) -> list[tuple[int, str]]:
        """Control records on this arm within reach that face traffic heading to the junction."""
        found = []
        nodes = self.snapshot.nodes
        walked, prev_id, prev_loc, way, cur = 0.0, arm.junction, arm.origin, arm.way, arm.toward[0]
        while True:
            walked += _distance(prev_loc, nodes[cur].location)
            if walked > REACH_M or self._is_junction(cur):
                break
            # traffic heading to the junction moves from cur to prev along `way`
            ids = way.node_ids
            travel_forward = ids.index(prev_id) > ids.index(cur)
            kind = nodes[cur].tags.get("highway")
            if kind in CONTROLS and self._faces(nodes[cur].tags, way, travel_forward):
                found.append((cur, kind))
            onward = [(w, nb) for w, nb in self._neighbours(cur) if nb != prev_id and nb in nodes]
            if len(onward) != 1:
                break
            prev_id, prev_loc = cur, nodes[cur].location
            way, cur = onward[0]
        return found

    def _neighbours(self, n: int) -> list[tuple[OsmWay, int]]:
        result = []
        for way, k in self.ways_at.get(n, ()):
            if k > 0:
                result.append((way, way.node_ids[k - 1]))
            if k < len(way.node_ids) - 1:
                result.append((way, way.node_ids[k + 1]))
        return result

    def _is_junction(self, n: int) -> bool:
        arms = 0
        for way, k in self.ways_at.get(n, ()):
            arms += (k > 0) + (k < len(way.node_ids) - 1)
        return arms >= 3

    @staticmethod
    def _faces(tags: Mapping[str, str], way: OsmWay, travel_forward: bool) -> bool:
        """Whether a control node on `way` faces traffic moving in the given way direction.
        A sign with no direction on a two-way street is left out: it does not say which way it faces."""
        direction = tags.get("direction") or tags.get(f"{tags.get('highway')}:direction")
        if direction == "forward":
            return travel_forward
        if direction == "backward":
            return not travel_forward
        if direction == "both":
            return True
        if direction is None:
            oneway = _oneway(way)
            return oneway != 0 and (oneway == 1) == travel_forward
        return False


def jev_state(c: RouteCrossing, facts: Sequence[_Fact]) -> dict[str, Any]:
    """What Jev is given about one crossing: its records as text, approach bearing and kind. The
    service and the corridor pre-run both build it here, so their cache keys match."""
    return {
        "main_street": c.main_street, "kind": c.kind, "approach_bearing": round(c.approach_bearing),
        "from_street": c.movements[0].from_street,
        "records": [f"{f.ref}: {f.claim}" for f in dict.fromkeys(facts) if f.source == "osm"],
    }


@dataclass(frozen=True)
class RuleVerdict:
    crossing: RouteCrossing
    status: Status
    state: dict[str, Any]
    open_question: bool  # Jev may settle it


def rule_verdicts(interpretation: Interpretation, snapshot: RecordSnapshot) -> list[RuleVerdict]:
    """The rules alone, for the corridor pre-run: settled crossings feed the gate, open ones the cache."""
    rules = _Rules(snapshot)
    out = []
    for c in interpretation.crossings:
        v = rules.crossing(c)
        out.append(RuleVerdict(c, v.status, v.state, v.status == "unknown" and v.ask))
    return out


def _apply_jev(c: RouteCrossing, verdict: _Verdict, advice: Advice | None, rules: _Rules) -> _Verdict:
    if advice is None:
        return verdict
    name = f"{c.key}.approach_control"
    answer = advice.answers.get(name)
    if answer is None:
        return verdict
    counts = advice.gate_passed and answer.probability >= THRESHOLD
    fact = _Fact(f"{advice.model_version}/{name}",
                 f"Jev: {answer.choice.replace('_', ' ')} (p={answer.probability:.2f})",
                 is_lead=not counts, source="jev")
    facts = verdict.facts + (fact,)
    # The question asks whether the main street's traffic stops for the driver (gapcross.jev).
    if counts and answer.choice == "does_not_stop":
        return _Verdict("flagged", f"{c.main_street} traffic does not stop for you, so you wait for a gap.", facts)
    if counts and answer.choice == "stops_for_us":
        return _Verdict("unflagged", f"{c.main_street} traffic stops for your crossing.", facts)
    return replace_facts(verdict, facts)


def replace_facts(verdict: _Verdict, facts: tuple[_Fact, ...]) -> _Verdict:
    return _Verdict(verdict.status, verdict.explanation, facts, verdict.ask, verdict.state)


def _failed() -> _Verdict:
    return _Verdict("check_failed", "This crossing couldn't be checked.")


def _wire(c: RouteCrossing, v: _Verdict, retrieved_at: datetime) -> Crossing:
    evidence = [Evidence(source=f.source, ref=f.ref, claim=f.claim, retrieved_at=retrieved_at, is_lead=f.is_lead)
                for f in dict.fromkeys(v.facts)]
    return Crossing(
        key=c.key, main_street=c.main_street, kind=c.kind, approach_bearing=c.approach_bearing % 360,
        along_m=c.along_m, location=c.location,
        assessment=Assessment(status=v.status, explanation=v.explanation, evidence=evidence),
    )


def _gap(m: Movement, main: str, kinds: set[str]) -> str:
    sign = "stop sign" if STOP in kinds else "yield sign"
    street = m.from_street or "your approach"
    return f"A {sign} faces {street}, and nothing recorded stops {main} traffic, so you wait for a gap."


def _word(kind: str) -> str:
    return {SIGNAL: "traffic signal", STOP: "stop sign", GIVE_WAY: "yield sign"}.get(kind, kind)


def _oneway(way: OsmWay) -> int:
    value = way.tags.get("oneway")
    if value in ("yes", "1", "true") or way.tags.get("junction") == "roundabout":
        return 1
    if value == "-1":
        return -1
    return 0


def _closest(arms: list[_Arm], bearing: float) -> _Arm:
    return min(arms, key=lambda a: abs((a.bearing - bearing + 540) % 360 - 180))


def _distance(a: LatLon, b: LatLon) -> float:
    k = 111_320.0
    return math.hypot((a.lat - b.lat) * k, (a.lon - b.lon) * k * math.cos(math.radians(a.lat)))


def _bearing(a: LatLon, b: LatLon) -> float:
    k = 111_320.0
    x = (b.lon - a.lon) * k * math.cos(math.radians(a.lat))
    y = (b.lat - a.lat) * k
    return math.degrees(math.atan2(x, y)) % 360
