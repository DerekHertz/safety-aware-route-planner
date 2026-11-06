# Abstract design: components, responsibilities, boundaries

Settled in the MVP grilling on 2026-11-05. Terms are defined in [CONTEXT.md](../../CONTEXT.md).
This is stage 1 of the method in `AGENTS.md`; types and interfaces come next.

## Scope

- **MVP:** a native iOS app that plans a Berkeley trip, compares the default route with at
  most one alternative, explains the crossings that change, and navigates the chosen route
  in the car with turn-by-turn guidance.
- **Tonight:** the same loop, driven by a simulated position in the iOS simulator. The
  service is built first, then the client on top of it.
- **Trips:** preset only. The first is Berkeley Bowl West to UC Berkeley. Its default route
  is an **example route** forced along Oregon St, labeled as supplied, because neither OSRM
  nor Mapbox chooses Oregon on its own.
- **Claims:** driver comfort and observable traffic controls. Never route safety.

## Components

```
                    ┌──────────────────────── service (WSL, server/) ───────────────────────┐
 iOS app            │                                                                       │
 (Mac, ios/)  ──API──▶  Comparison ──▶ Local search ──▶ Movement interpretation ──▶ Route source ──▶ OSRM
                    │      │                │                    │                                   │
                    │      └────────────────┴──▶ Assessment ◀────┘                                   │
                    │                              │   ├──▶ OSM records (Overpass)                   │
                    │                              │   └──▶ Jev adapter ──▶ Jev answer cache ──▶ Jev │
                    └───────────────────────────────────────────────────────────────────────────────┘
```

| Component | Responsibility | Boundary |
|---|---|---|
| **Route source** | Geometry, ETA, steps and intersections for an ordered list of waypoints. | The only code that talks to a routing provider. OSRM now; Mapbox Directions is a later swap behind the same seam. |
| **Movement interpretation** | Turn a route into ordered movements and crossings located along its geometry. Merges the carriageways of a divided main street into one crossing. | Reads route-source output and OSM structure. Knows nothing about controls. |
| **Assessment** | Give each crossing its status (flagged, unflagged, unknown, check failed) with evidence, dates and a short explanation. Rules over records first; Jev only for what rules leave unknown. | The only component that sees evidence sources or Jev. Its output type is all other components see. |
| **Local search** | Find alternatives that cross the same main street elsewhere, within the Oregon/Ward corridor and 3 blocks either side. Verify each candidate's actual geometry traverses the intended crossing in the intended direction, and have every introduced crossing assessed. | A search policy over the route source and assessment, testable without the network. |
| **Comparison** | Apply the budget and the improvement test, pick at most one alternative, and produce templated explanations. Distinguish "no improvement found", "incomplete search" and "check failed". | Pure logic over assessed routes. |
| **API contract** | The schema between service and app. | Checked into the repo. Neither side reads the other's code. |
| **iOS app: planning** | MapKit map with both routes, crossing markers, the avoidance toggle and marker explanations. | Displays the comparison. Holds no assessment logic. |
| **iOS app: follower** | Follows the selected route from a **position feed**: simulated (simulator) or CoreLocation (car). Next instruction, distance to it, voice prompt per turn, off-route banner with one-tap recompute. | Reads only the selected route, its instructions and markers. Playback speed never changes the ETA. |

## Settled rules

**Assessment**
- A flag needs positive evidence: the street is main, the driver's approach is stop- or
  yield-controlled, and nothing stops the main street's traffic. Absence of a record is unknown.
- Unflagged needs a recorded all-way stop or signal for the movement (a bare signal tag
  counts, never claims a protected left, and turns unknown on any conflict), or an
  established local cross street.
- Main street: OSM `highway` primary, secondary or tertiary, or an explicit record.
  Residential counts as local only when explicit. Anything else is unknown.
- A divided main street is one crossing; it is flagged if any carriageway needs a gap.
- Jev is text-only for the MVP. It answers approach control, road function and record
  conflicts, batched one request per route, cached by pinned model version plus an evidence
  and question hash, and pre-run for the corridor so it stays off the request path. A middling
  probability maps to unknown. Its answers are leads until it passes its gate: agreement with the
  rules on crossings the rules settle from clear records.
- No hand-labeling. Logic is checked with property tests. Disagreement between sources becomes
  unknown. Ground truth about the street comes from the owner's description of the two demo
  crossings and from a **drive report** after each real drive.
- The road-hierarchy convention (a local street stops for a main one) is a lead, never a status.

**Search and comparison**
- An alternative is offered only if its known flags plus unknowns are fewer than the default
  route's known flags. Rank by that total, then known flags, then ETA.
- Avoidance preference for the MVP is one on/off toggle: added time up to 20% of the default
  ETA, capped at 5 minutes. Over budget means "no improvement found within budget".
- Search targets 5 s, stops at 10 s with an incomplete-search status, and shows the default
  route as soon as it is available. The candidate limit is set after measuring request cost.
- ETA comes from the route source with the same departure assumption for both routes. No
  live traffic, no gap-wait or signal-wait adjustment. Labeled "estimated drive time" with
  "Traffic unknown".

**App**
- Markers on every main-street crossing of the displayed routes: `!` flagged, `?` unknown,
  a quiet check for unflagged. A failed check shows "Crossings couldn't be checked" and no
  markers. Marker counts always equal assessment counts.
- Crossing flags are explained before Start, not announced while driving.
- Plan and Start while parked; drive mode needs no touch; a passenger operates the phone on
  first drives.

## Environment and process

- One repo: `server/` built in WSL, `ios/` built by a Claude Code session on the owner's Mac.
- Parallel work: at most 5 workers. Skills: `grilling` and `domain-modeling` (abstract),
  `codebase-design` (concrete), `grill-the-flow` (flows and properties), `research` (source
  facts), `tdd` (implementation), `run` plus browser tools (verification), `code-review`.

## Parked

- Evidence store beyond the Jev answer cache, and a curated labels table.
- An agent pool for offline evidence enrichment.
- Street imagery for Jev (Mapillary, Street View).
- Mapbox Directions (blocked on its no-storing terms), live traffic, crossing-delay adjustment.
- Multi-level avoidance preference, destination entry, coverage beyond the corridor.
