---
Status: accepted
Date: 2026-09-25
---

# LLMs work at the edges: parse and explain per request, verify control data offline, never score or create routes

The owner wants an LLM layer on the route service, the **Trip planner**. A user types a
Request ("safest way, but not more than ten minutes longer"). The Trip planner turns it into
route-service inputs, selects one alternative (the Choice) and says why (the Explanation).
The owner also asked whether an LLM or agent could make the *safety judgement itself*
better, by walking a route and identifying intersection types along it.

This ADR records where an LLM is allowed in this system and where it is not. It came from a
grilling session on 2026-09-25. Vocabulary is in `CONTEXT.md` under "Asking and answering".

## Decisions

1. **The Trip planner is a consumer, not part of the engine.** It is a new top-level package,
   `planner/`, served as its own app. It reaches the route service only over HTTP (`/route`)
   and reads only route artifacts. As with `commute/` (ADR-0018), a boundary test stops it
   importing `api/`, `pyref/`, `sim/`, `core`/`sr_core` or `ingestion/`. It reuses
   `commute.tokens` for tester tokens and deploys alongside `commute/`. **The LLM never
   creates or edits a route.** Every Candidate Route is one of the service's alternatives.
2. **No LLM in per-request safety scoring.** Rejected: *a request-time agent walks each
   route and classifies its intersections.* The engine already classifies every turn, from
   the same OSM tags an LLM would read. An agent would add no information. It would add
   1-3 s per route, and it would make unsafe counts non-reproducible, which breaks the
   scenario suite ADR-0006 names as the validation contract (and ADR-0010's reason for
   keeping live speed out of the severity term).
3. **The LLM improves the safety judgement offline, on the input data.** ADR-0009 found
   that `ctrl_none & observed` is empty on both real packs: every counted uncontrolled
   crossing rests on an INFERRED control. OSM never tags "no signal here". That is where
   the core is weakest. **Control verification** runs offline:
   - Target: busy crossings whose control is INFERRED.
   - A vision model labels each one from Mapillary street-level imagery as **signal,
     stop, or none**. Protected-arrow detection is a separate stretch goal, attempted only
     after confirming what Oakland's `*BLT` fields mean.
   - Labels go into a **control overlay** keyed by OSM node and image capture date, and
     are applied at pack build as a new confidence, `VERIFIED`, next to OBSERVED and
     INFERRED.
   - An OBSERVED OSM tag is never overridden.
   - A label class counts like OBSERVED only if its measured precision is at least 95%.
     Otherwise it stays INFERRED. A label from imagery older than 3 years reverts to
     INFERRED.
   - Calls go through the Batch API.
   Query time stays deterministic, because the labels are frozen before any request.
4. **Honest baselines, fixed before results.** Each LLM component has a cheaper baseline it
   must beat:
   - The Choice must beat a rule, committed before the eval set exists. A strong safety
     Tradeoff picks `safe`, a strong speed Tradeoff picks `fast`, anything else picks
     `balanced`. If that route is not a Candidate, step toward `balanced`. Ties go to
     fewer unsafe actions.
   - Control verification must beat Mapillary's own `/map_features` detections on
     Oakland's signal inventory. If the detections alone are good enough, ship those and
     drop the vision model.
5. **Car first. Bike is a later phase.** Bike needs a bicycle network in the packs, LTS
   and elevation in ingestion, new cost terms under parity and the golden digests, and an
   amendment to ADR-0003's in-car thesis. That is larger than the whole LLM layer, which
   can be demonstrated on the car engine as it is. Stops (intermediate places) wait too:
   they raise the question of how three alternatives per leg combine.

## The Trip planner, v1

- **Inputs.** Origin and destination come from the map pickers. The Request says *how*,
  not *where*.
- **What the parser can produce** maps onto engine knobs that exist today:
  - Constraints: no unprotected lefts, no uncontrolled crossings, and "at most N% longer
    than fastest" (the detour budget).
  - A safety-versus-speed Tradeoff on a 5-level scale, -2 to +2, where ±2 means "strong".
  - Departure time. Relative times resolve against a clock passed in the prompt; evals
    freeze it.
  - Everything else is an **Unsupported clause**. It is named in the Explanation and never
    silently dropped. "No left turns" narrows to unprotected lefts, and the Explanation
    says so.
- **Infeasible Trips.** The car engine always returns three alternatives, so infeasible
  means none of them satisfies every Constraint. The Trip planner proposes a Relaxation
  naming the blocking Constraint and waits for the user to confirm it.
- **Grounded Explanations.** Every number and route name in an Explanation must match the
  route summaries the model was given. On a mismatch, regenerate once, then fall back to a
  template sentence.
- **Failure.** If the LLM fails or times out, the user gets today's three-route comparison
  with no Choice. The Trip planner is never worse than the app without it.
- **Model.** Claude Opus 5 at `low` effort, structured outputs validated against a Pydantic
  schema. A smaller model is the owner's call, made on measured latency and accuracy.
- **Privacy and cost.** No Request text is stored on the server; logs hold latency, token
  counts and parse-category counts. Production requires a tester token, plus a daily spend
  cap that returns 503.
- **Regions.** Every served pack. Control verification starts in Oakland, where the ground
  truth is.

## Measurement: v1 is done when all of these are measured

1. **Parse accuracy** (Constraint / Tradeoff / Unsupported) on about 100 labeled Requests:
   40 written by the owner, 60 synthesized edge cases, every label reviewed by the owner.
   Stored in `evals/requests.jsonl` and graded by exact match. The headline number is the
   30% held out from prompt tuning.
2. **Choice agreement** with the owner's own picks on 30+ real Trips, beside the rule
   baseline's agreement. Picks are made **blind**, before the Choice is revealed.
3. **Latency** of the LLM layer, target ≤ 2 s per Trip.
4. **Zero ungrounded Explanations** on the eval set.

CI replays recorded model replies, so it is free and repeatable. Live runs are manual and
report their cost.

For control verification:
- **Coverage gate, before any labeling.** At least 50% of target crossings must have
  Mapillary imagery under 3 years old facing the crossing, or the work is parked.
- **Precision and recall per class** against Oakland's 2024 signal layer, which is fetched
  at run time and never committed (it states no licence). The CC0 2013 set is the
  documented fallback. Emeryville's stop-sign layer is the only stop-sign ground truth, so
  stop precision is reported separately with that caveat.

## Terms and licences (checked 2026-09-25)

- **Mapillary** imagery is CC BY-SA 4.0. VLM analysis through the official API is allowed,
  and derived labels may be published under ODbL with `source=Mapillary` attribution
  (mapillary.com/osm). The access token is free.
- **Google Street View** is excluded. Maps Platform Terms §3.2.3(c) bars creating content
  from Street View imagery. Its own example is an index of tree locations, the same shape
  as our labels. §3.2.3(c)(vii) also bars using it to improve ML models.
- **KartaView** East Bay imagery is from 2016-2022, too old for current signal status.
  **Panoramax** has 13 pictures in the whole area.
- **No contribution back to OSM.** The Automated Edits code of conduct requires a
  documented, discussed, dedicated-account process that this project does not take on.

## Consequences

- Two new consumers of the artifact contract now exist beside the reference client:
  `commute/` and `planner/`. Neither may reach engine internals.
- The `VERIFIED` confidence changes the pack format and moves golden digests on purpose.
  That PR carries the usual parity and digest care.
- The Anthropic SDK becomes a dependency of `planner/` and of the offline verification
  job only, never of the route service.
- Order of work: deploy the commute service (Phase 4b (4c)) first, then Trip planner PR A
  and PR B, then control verification. See `docs/agents/handoff.md`.
