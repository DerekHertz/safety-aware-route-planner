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

## Amendment, 2026-09-20 — 1(3c) is built and measured; it is a regression, and the fix only reaches parity

1(3c) — reusable per-thread scratch buffers for `dist`/`pred` (`pyref/search.py`) and
`dist`/`pred`/`dest_adjust` (`core/src/engine.cpp`), replacing an `O(E)` allocation
done 4-6 times per request — was built (PR #48, draft). It is correct and
arithmetic-neutral. It measures slower.

Setup: real pack `berkeley_oakland` (E=20,683 edges / T=61,955 turns), WSL, `impl="cpp"`,
g++ 13.3.0 `-O2 -ffp-contract=off`, 40 fixed OD pairs, best-of-7 per pair in-process,
**18 interleaved processes per variant** with rotated order (a Latin square, to cancel
the cold-page-cache position bias on the first process of each round), compared pair by
pair. Three variants: **base** (pre-change, function-local `std::vector`s), **ref** (the
PR as written — `std::vector<T>&` references bound to a `thread_local` struct), and
**raw** (same scratch, but taking `double* dist = scratch.dist.data();` etc. once at the
top of `shortest_path`).

Headline, median-across-processes estimator:

| metric | ref-binding (the PR) | raw pointers |
|---|---|---|
| base median | 13.170 ms | 13.170 ms |
| new median | 13.468 ms | 13.209 ms |
| base total (40 pairs) | 534.23 ms | 534.23 ms |
| new total (40 pairs) | 548.00 ms | 534.89 ms |
| total delta | +13.76 ms (+2.58%) | +0.65 ms (+0.12%) |
| median paired delta | +0.321 ms (+2.61%) | +0.012 ms (+0.09%) |
| pairs faster with the change | 0 / 40 | 19 / 40 |
| sign-test p | 1.8e-12 | 0.87 |

Per-process sums (ms, n=18 each): base min 522.40 / med 534.86 / max 552.89; ref min
535.77 / med 549.53 / max 556.67; raw min 509.12 / med 536.62 / max 543.05.

**The codegen hypothesis was correct.** The PR's suspicion was that the slowdown is not
the fill or the allocator but the compiler losing the ability to hoist the buffers' data
pointers: as function-local non-escaping vectors it could keep them in registers, but as
references into a globally-reachable `thread_local` it cannot prove `heap.push` (which
may call `operator new`) does not alias them, so it reloads inside the relaxation loop
that runs up to T = 61,955 times a request. Raw pointers erase the regression (+2.58% →
+0.12%), which confirms this.

**But erasing it only buys parity.** Raw pointers land at +0.12% vs base, sign-test
p=0.87, 19/40 pairs faster — a coin flip. There is no net win. That is the number that
closes the item.

**Estimator sensitivity is a methodological warning worth keeping.** Under the
*min-across-processes* estimator that the original +1.85% result used, raw vs base reads
**−2.42%** (27/40 pairs faster, p=0.038) — i.e. it looks like a win. That reading is an
artifact: raw happened to draw one exceptionally quiet process (509.12 ms, vs 519.75 for
the next-lowest and a 536.62 median), and min imports that single process's luck into
every pair — visible as a contiguous block of pairs 16-37 all reading about −6%.
Min-across-processes is only safe when the variants' per-process sums are cleanly
separated — as they were in the original 4-process run, where every base process beat
every new one. That is not the case in this 18-process run: base spans 522.40-552.89 and
ref spans 535.77-556.67, which overlap, and base-vs-raw overlap almost entirely. When the
distributions overlap, use median-across-processes or 18+ processes, and report both.
(Base-vs-ref survives either estimator regardless, at 0/40 pairs faster and p=1.8e-12;
it is the base-vs-raw comparison that the choice of estimator actually decides.)
Two earlier 6-process replications contradicted each other on raw (+0.70% vs −1.09%) for
exactly this reason.

**Harness sanity check passed:** the ref build reproduced the originally recorded
+1.85% — it measured +1.97% under the original min-across estimator and +2.58% under
median-across, with two independent 6-round replications at +2.71% and +2.39%. Same
sign, same magnitude.

**`__restrict` needs no separate measurement:** adding `__restrict` to the three raw
pointers compiles to a byte-identical binary to plain raw pointers under g++ 13.3.0 at
`-O2` (same md5). It is the same machine code.

**Arithmetic-neutrality confirmed on the real pack:** all three builds emitted a
byte-identical output digest over the 40 real-pack routes (full-precision geometry,
unsafe dict, preference, maneuvers).

Disassembling `Engine::shortest_path` to observe the pointer reload directly is useless
— the symbol is a ~60-line stub in every build, because the body inlines into the pybind
wrapper. The differing binary hashes plus the timing are the evidence that stands in for
it.

**1(3c) is closed as measured-and-not-worth-it.** The cost side of the ledger: a
`thread_local`, a reset-semantics contract clause (P8, added in two files), a 248-line
concurrency test, and a non-obvious raw-pointer idiom — all for zero measurable gain. If
anyone revisits this, the raw-pointer variant is the only form worth considering, and it
must clear a bar meaningfully better than parity before it is worth paying that cost
again.

## Amendment, 2026-09-20 (later) — 1(3a-next) is done: `_cross_count` is gone, −44% of `compute_costs`

`_cross_count` — the node-level `np.add.at` histogram over every edge, gathered to
every turn, run twice a request (`edge_busy`, `edge_major`) — is deleted. It is replaced
by `_crossing_legs` (load time) plus `_uncontrolled_crossing` (per request) in
`pyref/costs.py`. `sr_core` does not move; this is a `pyref`-only change.

**The idea is not more lifting, it is noticing who reads the answer.** The count is
consumed in exactly one place, and only through a `> 0` test ANDed with three static
masks: `is_straight & observed & (ctrl_none | unprotected_approach)`. On
`berkeley_oakland` that gate is true for **2,865 of 61,955 turns (4.6%)**, so the old
code computed a histogram over all 20,683 edges and 61,955 turns to answer a question
asked at 2,865 of them — twice. Since only `> 0` is read, no count is needed either:
"does any other incoming approach at this node match" is an **any**, not a sum.

What the rewrite does, per gated turn: list the node's *other* incoming approaches
(everything but our in-edge and our out-edge's reverse) once per pack, padded to a
common depth by repeating one of them — a repeat cannot change an `any()`. Per request
it is one gather of shape `[depth, n]` (depth ≤ 4 here) and an `any` along axis 0. Turns
with nothing to cross can never answer True and are dropped at build time.

**It is exact, not merely close, and not by an FP argument** — the output is boolean, so
there is no ulp in it. The one premise is that the two excluded legs are distinct edges;
the old subtraction `node_in[head] − mask[inn] − mask[rev]` equals "how many others
match" only then. `ingestion/turns.py` forces `out == reverse(in)` to UTURN, so no
STRAIGHT can violate it — `_crossing_legs` asserts it at build time rather than assuming
it, and `tests/test_cross_count.py` pins the assert.

Setup, matching the 2026-09-20 amendment's methodology: real pack `berkeley_oakland`
(E=20,683 / T=61,955), WSL, `impl="cpp"` with `sr_core` built, 40 fixed OD pairs,
best-of-7 per pair in-process, **18 interleaved processes per variant** with the order
rotated each round, compared pair by pair. Both estimators reported, per the warning
recorded for 1(3c).

| `compute_costs`, per request | median-across | min-across |
|---|---|---|
| base median | 3.394 ms | 2.821 ms |
| new median | 2.012 ms | 1.553 ms |
| base total (40 pairs) | 133.02 ms | 115.25 ms |
| new total (40 pairs) | 74.15 ms | 66.13 ms |
| total delta | **−58.87 ms (−44.3%)** | **−49.13 ms (−42.6%)** |
| median paired delta | −1.390 ms (−41.1%) | −1.271 ms (−45.1%) |
| pairs faster | 40 / 40 | 40 / 40 |
| sign-test p | 1.8e-12 | 1.8e-12 |

| whole `POST /route` | median-across | min-across |
|---|---|---|
| base median | 14.167 ms | 12.866 ms |
| new median | 13.192 ms | 12.079 ms |
| total delta (40 pairs) | **−39.08 ms (−6.77%)** | **−36.36 ms (−6.84%)** |
| median paired delta | −0.990 ms (−6.9%) | −0.883 ms (−7.0%) |
| pairs faster | 40 / 40 | 40 / 40 |
| sign-test p | 1.8e-12 | 1.8e-12 |

Per-process sums (ms, n=18 each): base min 534.03 / med 577.97 / max 649.09; new min
502.37 / med 534.66 / max 664.63 — overlapping distributions, which is exactly why the
paired, both-estimator reading is what the conclusion rests on; the unpaired per-process
median delta is −43.31 ms at permutation p=0.0038. **Unlike 1(3c), the two estimators
agree and every single pair moves the same way.**

Replicated on `berkeley_small` (E=1,827 / T=5,365), 10 processes per variant:
`compute_costs` −35.1% (0.243 → 0.158 ms), whole request −2.78% (3.053 → 2.984 ms),
40/40 pairs faster, p=1.8e-12. The win shrinks with pack size, as expected — the gated
fraction is what it scales with, not the edge count.

**Load-time cost: +3.3 ms once per pack** (Router construction 72.1 → 75.4 ms), which
buys −1.39 ms on every request after it.

**The fused-bit variant was built and is slower — do not try it again.** Encoding
busy/major as bits of one `uint8[E]`, gathering once over the union of both gated turn
sets and testing a per-turn threshold measured **+9.6% median-across / +1.2% min-across
on `compute_costs`** and +0.67% on the whole request (3/40 pairs faster, p=2.0e-08)
against the split form that shipped. The reason is structural: `ctrl_none & observed` is
**empty on both real packs** (an untagged approach is never OSM-observed), so the split
form's busy gather is zero-width and the major gather is the only work, while fusing
re-adds an `O(E)` bit-combine and a wider reduce to serve a set that is empty. Two
gathers over disjoint sets beat one gather over their union here.

**Arithmetic and output neutrality:** `tests/test_costs_golden.py`'s 495 digests are
unchanged, and all 36 benchmark processes across both variants emitted one identical
route digest (geometry, unsafe counts, segments, maneuvers, preference) over the 40
real-pack routes. The golden corpus is toys only, so `tests/test_cross_count.py` adds
the real-pack half: it keeps the old algorithm as an oracle and compares on both real
packs over random masks (including `major ⊄ busy`, which `compute_costs` never produces)
and at six real departure times.

**What is left in `compute_costs` is now genuinely per-query and evenly spread** — the
volume gather and norm, `raw`'s arithmetic and its two mask multiplies, the penalty
clamp, the busy/major masks, `over_tau`, the unprotected-left predicate and the two tier
scatters, all `O(T)` numpy with no remaining `O(pack)` scatter. It is ~2.0 ms of a
~13.2 ms request (15%, down from 24%). There is no single item left worth naming; the
next real step down would be evaluating the whole per-query half in one pass, which is
the laziness this ADR already ruled out on parity grounds.
