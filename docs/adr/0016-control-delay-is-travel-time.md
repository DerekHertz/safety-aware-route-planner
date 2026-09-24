---
Status: accepted
Date: 2026-09-24
---

# Control delay is travel time: the fast route pays for waiting at the intersection

The engine's time term is **link travel time only**. `arc_cost = turn_time_s + λ·penalty`,
and `turn_time_s` is the target edge's traversal time. Nothing charges for waiting at the
intersection. So at λ=0 an uncontrolled crossing of a four-lane arterial is exactly as
fast as a signalized one, and the `fast` route takes it. Only the safety penalty, under λ,
knows the crossing is there.

That is wrong as time, not just as safety. The case that surfaced it was a real grocery
run: Google Maps sent the driver twice across a busy four-lane street with no signal and
oncoming traffic that did not stop. Each crossing took **more than a minute** of waiting
for a usable gap. A fully signalized intersection a block or two away would have been
safer and probably *faster*: the light turns green sooner than a comfortable gap appears.
Our own `fast` route makes the same mistake, for the same reason.

**Decision: add an expected control delay per turn to the time term.** It is time, not
penalty. It enters `eta_s`, `detour_pct` and the λ=0 search, and is independent of λ.

## The model

Control delay depends on how many cars are on the conflicting road (**exposure**), not
how fast they go. ADR-0010 established that live speed feeds cannot measure exposure,
so the input is the same `[sim]` volume the busy rule already uses.

| Movement at the approach | Expected delay |
|---|---|
| Right of way (major-road through, protected arrow) | 0 |
| Gap acceptance: uncontrolled crossing, left or right onto a road from a stop or no control | single-vehicle gap wait against the conflicting flow, capped |
| Signal | per-class average wait, lower for the major-road through movement than for crossing it or turning left across it |
| Permissive left at a signal | signal wait plus a gap wait against the oncoming flow |
| All-way stop | small fixed wait (~5-8 s) |

The gap wait's starting form is **Adams' delay** for Poisson traffic,
`E[w] = (e^(q·t_c) − q·t_c − 1) / q`. Here `q` is the conflicting flow, summed over the
lanes and directions the movement must clear, and `t_c` is the critical gap for the
movement. Starting critical gaps follow the Highway Capacity Manual's two-way-stop base
values: roughly 6.5 s for a crossing, 7.1-7.5 s for a left onto the major road, 6.2-6.9 s
for a right, and 4.1 s for a left from the major road across oncoming traffic. Worked
through, a crossing of ~2,000 veh/h (four lanes at 500 each) comes to ~60 s, and a left
into the same road to ~105 s. That is the order of the grocery-run wait. At 3 am, at ~150
veh/h, it is about a second.

**Cap at ~120 s.** The formula grows without bound as flow approaches saturation; a real
driver gives up and goes around, which is the search's job, not the delay term's.

Rejected: the full HCM control-delay formula with queueing. It needs the side street's
own volume and a queue model, and there is one routed vehicle whose wait we are pricing.
Revisit if trip-trace calibration (ADR-0017) shows single-vehicle waits systematically
under-predicting at busy side streets.

**Every constant is config, and covered by `traffic_basis.profile_version`.** Today that
field hashes `[sim]`; if the delay constants live elsewhere, the hash is extended to
include them. Calibration (ADR-0017) then changes numbers, never code, and every
calibrated artifact says which numbers it used.

**Guessed control still gets a delay.** An INFERRED approach carries the delay of the
control it was inferred to have. The OBSERVED gate in `pyref/costs.py` protects the
*unsafe-action count*: a guess is not evidence of a hazard. Delay is time, and the best
estimate of time uses the best guess of control.

## Contract

Additive, so `schema_version` stays 2 (ADR-0004):

- `UnsafePoint.expected_wait_s`: the control delay at that unsafe maneuver. This is the
  "these three unprotected lefts may add about X minutes" line in the fast-vs-safe
  comparison.
- `RouteAlternative.control_delay_s`: the route's total control delay over every
  intersection, signals included. It is already inside `eta_s`; this field makes it
  legible.

Both are mirrored into `web/lib/types.ts` and `check-schema-sync.mjs` `PAIRS` in the
same PR.

## Consequences

- **Amends ADR-0006.** "Base routing is plausible, not competitive" stands, but base
  routing is no longer *link time only*. The fast route now avoids a bad crossing when
  the crossed road is actually busy, and takes it when it is not.
- **Costs change on purpose.** The delay is computed in numpy in `compute_costs` and
  added to `turn_time_s`, so both engines still consume one finished array and parity is
  untouched. `tests/test_costs_golden.py` digests move and are re-pinned in the same PR,
  with the change called out, not silently regenerated.
- **Most of the delay is per-pack static.** Movement type, control, critical gap and the
  conflicting legs depend only on (pack, cfg), and belong in `PackStatics`. Per request
  it is one gather of conflicting volume plus an `exp`. Measure it against ADR-0009's
  numbers; `compute_costs` is ~2 ms.
- **The A\* heuristic stays admissible.** Delay is non-negative, so a straight-line
  bound on link time is still a lower bound.
- **ETAs rise across the board**, most on routes through busy stop-controlled
  intersections. That is the correction, not a regression.
- **Scenario test:** the grocery run as a toy. A busy four-lane road with an uncontrolled
  crossing on the direct line and a signal 1-2 blocks away. At peak volume the `fast`
  route takes the signal; at 3 am it takes the direct crossing.
