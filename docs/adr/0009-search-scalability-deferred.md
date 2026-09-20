---
Status: proposed (amended 2026-09-17)
---

# Search scalability for larger regions (deferred)

**Open question, not yet decided.** The current search is Dijkstra / A\* over the
edge-based turn graph, which is sub-millisecond on a single metro pack but would not scale
to a much larger search space (a whole region or country). Region-agnostic *correctness*
is settled (ADR-0007); region-agnostic *scale* is not.

This is recorded so the limitation is explicit rather than discovered later. Directions to
theorize about when it becomes relevant: contraction hierarchies, boundary/overlay graphs,
tiled packs with cross-tile stitching. Each interacts non-trivially with the edge-based
turn model and the Python↔C++ parity guarantee, so none is a drop-in. No option is chosen
yet.

## Amendment, 2026-09-17

Two findings that change this ADR's framing without resolving it.

**Contraction hierarchies are almost certainly *not available* to this project**, so
listing them as a neutral option was misleading. CH assumes a **static scalar metric**.
This engine's metric is time-dependent (`sim.snapshot.at_time`), *lambda*-parameterized
(three safety levels per request), and re-weighted mid-request by the penalty-method rerun
in `pyref/alternatives.py`. Time-dependent CH exists but is research-grade, and the lambda
sweep would need a separate hierarchy per level. Boundary/overlay graphs and tiled packs
with cross-tile stitching remain open; CH should not be picked up without confronting this
first.

**The first scalability blocker is not the search algorithm.** `pyref/costs.py`
`compute_costs` builds full-graph numpy arrays over **every turn in the pack on every
request**, `heuristic` haversines every edge head, and `arc_cost` allocates another
full-graph array **per lambda and per rerun** (3-8 times per request). At 61,946 turns
that is trivial; it scales linearly with pack size and becomes the binding constraint at
roughly one large metro, well before the search algorithm does. Evaluating turn cost
lazily over the explored frontier instead of materializing the whole graph is both cheaper
than any option listed above and a prerequisite for most of them.

**Related decision:** ADR-0006's identity question was settled in favour of keeping this
project's own engine rather than adopting Valhalla or GraphHopper, on the grounds that the
safety cost model (approach-control confidence gating, per-maneuver control overrides, the
lambda sweep, the detour budget, the penalty-method reruns) is non-standard enough that
porting it into a foreign costing abstraction would mostly deliver parity with what
already exists, at the cost of the Python/C++ parity suite that proves the model correct.
Revisit if coverage commits to nationwide, or if production map-matching is needed (which
the learned-habitual-route idea in ADR-0011 would require, and Valhalla ships Meili).

## Amendment, 2026-09-18 — measured, and the item is mis-aimed

The 2026-09-17 amendment named the right bottleneck and then mis-stated its size in
both directions. Measured on `berkeley_oakland` (20,678 edges, 61,946 turns), WSL,
with **`sr_core` built** — the correction that matters, because the previous numbers
were evidently taken against the pure-Python engine:

| | |
|---|---|
| `compute_costs` | 6.4 ms |
| full `POST /route`, C++ engine, median | 17.7 ms |
| **`compute_costs` as a share of one request** | **36%** |

So *"At 61,946 turns that is trivial"* is **false for the configuration that ships**.
It is trivial only next to pyref's ~18 ms-per-search, which production does not run.
The bottleneck is real, and it is real now, at one metro.

Three corrections to the same amendment:

- **`arc_cost` being called 3-8 times per request is not a time cost.** It is 0.1 ms a
  call, ~2% of the request. Its sin is allocation churn, not latency. Do not build a
  plan around it.
- **The explored frontier is not small.** A-star settles **31-71% of edges (mean ~49%)**
  on random OD pairs, only modestly better than Dijkstra — the heuristic is
  straight-line over pack-wide max free-flow speed with no safety term, so it goes
  slack as soon as lambda > 0. Lazy-over-frontier therefore buys about **2x** at metro
  scale, not the order of magnitude the framing implies.
- **Both engines already allocate `O(E)` per search call** (`np.full` in
  `pyref/search.py`; three `std::vector`s in `core/src/engine.cpp`), 4-6 times a
  request. Any plan that makes costs regional and leaves this alone has solved half the
  problem.

### Laziness is the one lever that costs bitwise parity

Parity is currently a *design consequence*: all FP happens once in numpy and both
engines do identical trivial additions. Every lazy design breaks that. Evaluating costs
in C++ duplicates the model across two compilers — and `heuristic` calls
`haversine_m`, whose sin/cos/asin differ in the last ulp between MSVC and glibc, so
parity dies outright and no compiler flag saves it. Having pyref call into `sr_core`
deletes the pure-Python fallback *and* the parity suite's reference. Calling back into
Python from C++ reacquires the GIL inside the relaxation loop, destroying the
GIL-release that the Dockerfile's whole "one process saturates every core" story rests
on.

### What to do instead

**A pure-numpy hoist, no engine change, no parity risk.** Everything in `compute_costs`
except the volume term is a function of `(pack, cfg)` only; departure enters solely
through a length-14 per-class gather. Hoisting the static half to load time, computing
the heuristic over the 7,684 *nodes* rather than the 20,678 edge heads, and lifting the
`edge_time_s` gather out of `arc_cost` were each verified **bitwise identical** on the
real pack and remove roughly 60-70% of the per-request `O(pack)` cost. This is a
`pyref`-only change, so it does not violate "one large PR" — that instruction is about
`pyref` and `sr_core` moving *together*, and here `sr_core` does not move.

**If a pack ever exceeds roughly 5x a metro**, the answer is to make the precompute
*regional* rather than the engines *lazy*: fill a provably-sufficient sub-region of the
same full-size array and leave the rest at `+inf`. Both engines still receive one
finished array, so every FP op still happens once in numpy and parity is untouched;
`g + inf` never relaxes, so no engine code changes to make it safe; and a route whose
cost lands inside the region's bound is provably exact, with a doubling retry degrading
to today's behaviour. The `_cross_count` one-hop halo is the subtle part.

**Phase 4's dependency on this item is backwards.** Pack-per-metro selection keeps each
pack metro-sized — it needs a pack registry and a memory budget for N resident packs,
not lazy costs. The gate should be removed.

**Unverified:** whether the region stays tight at lambda = 1.5, where the time-only
heuristic badly under-estimates generalized cost. If it does not, the honest conclusion
is that the **heuristic** is what to fix first, and this item never happens.

## Amendment, 2026-09-18 (later) — 1(3a) is implemented; the estimate was high

The pure-numpy hoist described above is done, and the prediction it rested on was
worth checking. Measured in situ under `cProfile`, over 40 real `Router.route()`
calls on `berkeley_oakland` with the C++ engine (cumulative seconds):

| | before | after | |
|---|---|---|---|
| `compute_costs` | 0.172 | 0.136 | |
| `heuristic` (40x) | 0.025 | 0.009 | |
| `arc_cost` (160x) | 0.020 | 0.015 | |
| **total `O(pack)`** | **0.217** | **0.160** | **-26%** |

End-to-end that is roughly **-1.4 ms on a ~14 ms request, about 10%**, reproduced
over three runs. Load-time cost is 4.4 ms once per pack.

**"Roughly 60-70% of the per-request `O(pack)` cost" was too optimistic — it is
26%.** The static half was correctly identified; the error was assuming it
dominated. What remains is genuinely per-query: the volume gather, the raw
arithmetic and its two multiplies, the penalty clamp, the busy masks, `over_tau`,
the unsafe predicates and the tier scatters.

**`_cross_count` is now the single largest item left**, about 45% of what remains
in `compute_costs` (~1.55 ms a request). It runs twice — once over `edge_busy`,
once over `edge_major` — and both masks are volume-dependent, so it cannot be
hoisted the way the rest was. Its index arrays already are. Anyone continuing this
item should start there rather than looking for more to hoist.

**The 2026-09-18 amendment's `arc_cost` finding stands, and was nearly overturned
in error.** An isolated timing loop suggested 0.6 ms a call, which would have made
it the largest single win; in situ it is ~0.12 ms, matching the 0.1 ms recorded
above. The isolated number was a benchmark artifact. Treat tight-loop timings of
these functions as unreliable — the allocation pattern differs from a real request.

**Bitwise neutrality held**, verified two ways: every `QueryCosts` field, `arc_cost`
at three lambdas and `heuristic` byte-identical to the pre-hoist implementation on
the real pack, and the golden digests in `tests/test_costs_golden.py`. The
heuristic's node-gather identity — the one step with a real ulp risk, since it
applies sin/cos/arcsin to a different-length array — holds on both real packs.
