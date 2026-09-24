# Handoff: what's next, and where the plan lives

The durable answer to *"what should I build next?"* for a fresh agent session.
Keep this current: update the **Now / Next** list whenever a feature merges. This
file references the real artifacts (ADRs, PRs, issues) instead of duplicating
them — follow the links.

## Where the plan lives

- **Feature sequencing** is driven by the ADRs in `docs/adr/`. The current spine
  is the **"robust core" milestone** in
  [`docs/adr/0008-live-nav-parked.md`](../adr/0008-live-nav-parked.md) — the
  ordered bar to un-park live navigation.
- **Tickets**, when opened, are GitHub issues (see
  [`issue-tracker.md`](./issue-tracker.md)). Check `gh issue list` first.
- **Vocabulary** is fixed in [`../../CONTEXT.md`](../../CONTEXT.md); don't blur
  safety **level** (route) vs **tier** (segment).

## Now / Next (update on every merge)

The robust-core milestone (ADR-0008) is **complete**; that ADR is resolved. The current
spine is the **scalability + commute-planner** sequence below, decided 2026-09-17 and
recorded in ADR-0010, ADR-0011, ADR-0012 plus amendments to ADR-0005 and ADR-0009.

**Phase 0 - record the decisions.** _(this PR)_

- [x] ADR-0010 real traffic deferred, ADR-0011 commute planner, ADR-0012 PWA client.
- [x] Amend ADR-0005 (`busy_floor_by_class`), amend ADR-0009 (CH is not available, and the
      real blocker is per-request full-graph cost), resolve ADR-0008.
- [x] `CONTEXT.md` vocabulary: busy floor, traffic basis, commute planner, commute,
      departure-time sweep, disruption.

**Phase 1 - unblock scale.** Nothing else should start before (1).

- [x] **(1) Decouple geocoding from the process model.** Self-hosted Photon, or move the
      limiter to Redis. Today `api/geocode.py` funnels every user through one global
      ~1 req/s lock, which does not throttle at the edge -- it *serializes*, so N
      concurrent typists wait N seconds. It is also why the README and Dockerfile mandate
      a single process with no `--workers`, pinning a CPU-bound engine to one replica for
      a text-search side feature. Delete those constraints as part of this. **First code
      PR; everything about scale is downstream of it.**
- [x] **(2) Rate-limit `POST /route`.** Currently unauthenticated, no quota, 3-8 graph
      searches per call, one process. Should not wait for users to exist.
- [ ] **(3) Per-request `O(pack)` cost — RE-SCOPED 2026-09-18, do not build as written.**
      Measured with `sr_core` built: `compute_costs` is **36% of a real request** (6.4 ms
      of 17.7 ms) at 61,946 turns, so the bottleneck is real *now*, not at "one large
      metro". But the fix named here is wrong: A-star settles ~49% of edges, so
      lazy-over-frontier buys ~2x at metro scale, and it is the one approach that costs
      bitwise parity (`heuristic` haversines, and MSVC vs glibc sin/cos differ in the
      last ulp). See ADR-0009's 2026-09-18 amendment for the measurements.
      **Do instead, in order:**
  - [x] (3a) **Pure-numpy hoist — `pyref` only, verified bitwise identical.** Done.
        `PackStatics` holds the (pack, cfg)-only half and is built once in
        `Router.__init__`; the heuristic haversines nodes not edge heads; `arc_cost`
        consumes a pre-gathered `turn_time_s`. Bitwise neutrality is pinned by
        `tests/test_costs_golden.py` (495 digests) and was cross-checked against the
        pre-hoist module side by side on the real pack.
        **Measured -26% of per-request `O(pack)`, not the estimated 60-70%** — about
        -1.4 ms on a ~14 ms request. See ADR-0009's second 2026-09-18 amendment for
        the table and for two corrections worth reading before continuing this item.
  - [x] (3a-next) **DONE 2026-09-20 — `_cross_count` is deleted; `compute_costs` is
        -44%.** The lever was not lifting but noticing the consumer: its `> 0` answer
        is read at only `is_straight & observed & (ctrl_none | unprotected_approach)`
        — **2,865 of 61,955 turns (4.6%)** — and only as an *any*, never as a count.
        `_crossing_legs` lists each gated turn's crossed approaches once per pack;
        per request it is one gather plus an `any`. Exact by construction (boolean
        output, no ulp), the one premise — the two excluded legs being distinct edges
        — asserted at build time. Measured on `berkeley_oakland`, 18 interleaved
        processes a variant: `compute_costs` **3.39 -> 2.01 ms (-44%)**, whole request
        **14.17 -> 13.19 ms (-6.8%)**, 40/40 pairs faster, sign-test p=1.8e-12, both
        estimators agreeing; +3.3 ms once per pack at load. Golden digests unchanged;
        `tests/test_cross_count.py` holds the old algorithm as a real-pack oracle.
        The **fused-bit** variant (busy/major as bits of one array, one gather over
        the union) was built and is **slower** — see the amendment before retrying it,
        as with the older `np.bincount` attempt. ADR-0009's second 2026-09-20
        amendment has the tables and what is left in `compute_costs`.
  - [ ] (3b) *Only if a pack ever exceeds ~5x a metro:* make the precompute **regional,
        not lazy** — fill a sub-region of the same full-size array, `+inf` elsewhere.
        Parity untouched, because both engines still receive one finished numpy array.
  - [ ] (3c) **CLOSED 2026-09-20 — built, measured, not worth it.** The genuine
        lockstep PR: reusable scratch buffers for `dist`/`pred` (`pyref/search.py`) and
        `dist`/`pred`/`dest_adjust` (`core/src/engine.cpp`), which both allocate `O(E)`
        per search, 4-6 times a request. Built and correct (PR #48, draft), but it
        measured **+2.58% slower** as written — confirmed codegen: binding the
        buffers as `thread_local` references stops the compiler hoisting their data
        pointers out of the relaxation loop. Rewriting to raw pointers erases the
        regression, but only down to **+0.12%, p=0.87** — parity with baseline, not a
        win. Do not rebuild this. If revisited, the raw-pointer form is the only one
        worth trying, and it must clear a bar meaningfully better than parity. See
        ADR-0009's 2026-09-20 amendment for the measurements.

**Phase 2 - cheap wins, parallel to Phase 1.**

- [x] Fast-vs-safe comparison UI. **Zero backend**: one `/route` response already carries
      all three alternatives with `UnsafeCounts`, per-segment tiers and `detour_pct`.
      Best effort-to-value ratio in the plan.
- [x] `busy_floor_by_class` + a scenario test pinning the 3am arterial (ADR-0005).
- [x] Screen Wake Lock, with `visibilitychange` reacquisition (ADR-0012).

**Phase 3 - contract, then nav.**

- [x] `traffic_basis` in `preference`, `schema_version` -> 2. Done 2026-09-19; see
      ADR-0004's "v2 as shipped" section. Nested `{source, as_of, profile_version}`, minted
      in `sim/snapshot.py` so the future feed swap is a value change; **required on output,
      optional on input** via a sibling `CarriedPreference` model, so a client mid-drive
      holding a v1 artifact can still reroute. `as_of` duplicates `departure_time` under the
      synthetic model and is documented as doing so; `profile_version` (a hash of `[sim]`)
      is the half that is real information today.
- [ ] Promote live nav off `NEXT_PUBLIC_ENABLE_LIVE_NAV` (ADR-0008's resolution: wake lock
      + one real field drive).

**Phase 4 - multi-metro.** Pack-per-metro selection and routing. **No longer gated on
Phase 1(3)** — that gate was backwards: pack-per-metro keeps each pack metro-sized, so it
needs a pack registry and a memory budget for N resident packs, not lazy costs.
**Designed in [ADR-0014](../adr/0014-pack-registry-multi-metro.md) (accepted; merged as #62)**
— read it before starting. The short version: config names the served packs, their
bboxes must be disjoint, and a request goes to the pack that contains both of its
endpoints. Otherwise it gets a 422 with the existing `{detail}` shape, and no `/route`
wire field changes. All packs are loaded eagerly. Measured cost: about 20 MiB
resident per `berkeley_oakland`-sized pack. The ADR ends with a 7-step PR sequence;
work through it in order:

- [x] (1) `PackRegistry` of one + pure `pack_for(o, d)` + startup validation. Done
      2026-09-23 in `api/registry.py`. Pinned: points are `(lat, lon)`, bboxes
      `[west, south, east, north]`; containment is closed, so **touching bboxes count
      as overlap**; a null bbox contains everything and is legal only alone.
      `SR_PACK_DIR` names its pack from the manifest `region`, not the directory.
      Handlers still read `registry.only()`; `pack_for` is not wired in until (3).
- [x] (2) `[api] regions` / `SR_REGIONS`, eager load of N packs, real `/health` count.
      Done 2026-09-23. `served_regions(cfg)` in `api/registry.py`: `SR_REGIONS`, else
      `[api] regions`, else `[region.active]` (shipped config leaves the key commented
      out, so one-pack deployments are unchanged). `lifespan` fetches and loads every
      served pack before `/health` is ready; `/health` now reports `packs_loaded`,
      `regions` (a list; the old scalar `region` key is gone) and summed `num_edges`.
      Each `PackEntry` carries its own `tz`; `AppState.pack_tz` is gone. Tests in
      `tests/test_multi_pack_load.py` point `SR_CONFIG` at a rewritten config whose
      `pack_dir` is `tmp_path` — no new env hook (the toy-metro helpers now live in
      `tests/helpers/multi_pack.py`).
- [x] (3) Route by coordinates; the 422 contract, on `/route` and `/reroute`. Done
      2026-09-24. `api/routes.py::_select_pack` calls `registry.pack_for(o, d)` after
      the quota dependency and before any search, and raises 422 with the selection
      error's `.detail`; `/reroute` re-derives from position + destination. Tests in
      `tests/test_route_by_coords.py` (spies on each served `Router`, a recording
      limiter for the spent token, the null-bbox `SR_PACK_DIR` toy still getting the
      snap-failure 422, and a byte-identical `berkeley_oakland` artifact through the
      registry vs a direct `Router`). The "still one-pack only" gaps noted here are
      closed by (5) and (6): no handler reads `registry.only()` any more, and
      `AppState.pack`/`.router` are gone.
- [x] (4) Per-pack IANA timezone for departure (#72, done before (2) and merged into
      it). `api/departure.py`: `resolve_departure` + `pack_timezone`; `timezone` on
      each `[region.presets.*]`, missing on a served pack = startup failure.
- [x] (5) `/geocode?region=`, bounding per pack (#75). Unknown region is 422 before
      cache/bucket; several packs and no region = one unbounded query (limit 20),
      post-filtered to served bboxes, top 5 returned. Cache keys on `(q, region)`.
- [x] (6) Additive `/meta.packs` and the client: initial view, coverage check, and a
      cross-region pre-flight. Done 2026-09-24. `/meta` lists every served pack
      (`ServedPack`, default first) and keeps the top-level fields as the default pack;
      tests in `tests/test_meta_packs.py`. Client helpers are pure and live in
      `web/lib/coverage.ts` (`packForPoint`, `insideCoverage`, `initialViewBbox`,
      `preflight`, `normalizeMeta` for a server without `packs`). `MapView` has no
      hard-coded center: it is not constructed until `/meta` settles, then frames the
      pack containing the GPS fix (else the default pack), or the whole world if
      `/meta` failed or the pack has no bbox. The geocoder sends `region` = the pack
      the map center is in, else the GPS fix's. With ≥2 packs, every endpoint
      (`/route`, `/reroute`, `/geocode`, `/meta`, `/health`) now answers without a
      500. `AppState.pack`/`.router` were removed (only tests used them);
      `PackRegistry.only()` stays, for tests only.
- [ ] (7) Build a second real metro and **re-measure memory in the container**. The
      metro-scale figure in the ADR is a guess. Paused and then **un-paused 2026-09-24**
      (ADR-0015 amendment): pick a **dense urban** metro. That serves trip-trace
      calibration (ADR-0017) now and ADR-0015's benchmark if its terms gate G0 ever
      clears. Sequenced in **Phase 4b** below, after items 1-2.

**Phase 4b - control delay and trip traces.** Decided 2026-09-24 in a grilling session;
recorded in ADR-0016, ADR-0017, and amendments to ADR-0006, ADR-0010, ADR-0011 and
ADR-0015. **Google Routes is not adopted.** Our router stays the product, and
Google-as-router waits on ADR-0015's G0 terms clearance. Work in this order:

- [x] (1) **Control delay in the time term** (ADR-0016). Done 2026-09-24 (#84).
      Constants live in `[sim.control_delay]`, so `profile_version` covers them. Waits are
      in `turn_time_s` and ETA. `UnsafePoint.expected_wait_s` and
      `RouteAlternative.control_delay_s` are on the wire. The grocery-run test is
      `tests/test_scenario_control_delay.py`. 99 golden `arc_cost` digests moved on
      purpose, and 45 `turn_delay_s` digests were added. `compute_costs` +0.33 ms.
      Things to know before touching it:
  - **The exp is `_exp_poly`, not `np.exp`.** It is a fixed polynomial, so the digests
    do not depend on numpy's SIMD dispatch. There is no direct test of it against
    `np.exp` yet (small follow-up).
  - **Which road counts as major:** the one with the strictly higher road class, or the
    road that runs through a T-junction. Equal-class signal junctions give both approaches
    the 22 s minor wait.
  - **The 120 s cap covers only the gap wait,** so a permissive left at a signal can reach
    142 s.
  - **Open questions for calibration (ADR-0017):**
    - whether equal-class signals should get 22 s;
    - whole-road volume overcounts the traffic a turn actually waits for;
    - the crossing legs overcount at some T-junctions.
  - **"Fast takes the unprotected left" scenarios moved to 3 am.** At peak they now take
    the signal, which is the intended behavior.
  - **Not done yet:** the comparison UI does not display the new fields. Next small web PR.
- [x] (2) **"Open in Google Maps" link** (#82): "Compare in Google Maps ↗" under the route
      cards. It uses `web/lib/googleMapsLink.ts`, a Maps URLs Directions link with no key.
      Maps URLs have no departure-time parameter, so Google plans for "now".
- [ ] (2b) **Show the waits in the comparison UI.** "Fast: 2 uncontrolled crossings, ~3
      min waiting" from `control_delay_s`, and the per-marker `expected_wait_s`. Client
      only; the fields are already in `types.ts`.
- [ ] (3) Phase 4 item (7) above. **This is next.**
- [ ] (4) **Trip-trace collection** (ADR-0017). The first slice of the commute planner
      service: one ingest endpoint and a tester token per device. The client buffers to
      IndexedDB, uploads chunks about every 2 minutes and at trip end, and trims 300 m
      at each end of the trip on the device. Raw traces are kept 90 days. **Move this
      ahead of (3) if beta testing starts first**, or those drives go uncollected.
- [ ] (5) **Wait extraction and calibration** (ADR-0017). Match traces against the
      followed route, count time below 2 m/s in the last 60 m, and fit ADR-0016's
      constants on pooled data. The output is config, never live lookups.
- [ ] (6) **Free traffic sources** (ADR-0010 amendment, rung 2): 511 WZDx closures as
      hard blocks, PeMS freeway speeds. The HERE corridor feed (rung 3) waits for
      measured ETA error and a check of HERE §6.4(b), its ODbL clause.

**Phase 5 - commute planner** (ADR-0011). Google Sign-In, accounts, saved commutes with
user-set departure times, the **departure-time sweep** as the headline feature, a stubbed
disruption-source interface, a daily brief, and predicted-vs-actual ETA logging. The
service itself starts earlier, as Phase 4b (4)'s trip-trace ingest (ADR-0011 amendment).

**Phase 6 - trigger-gated** (see ADR-0010's triggers and its 2026-09-24 ladder). Free
feeds are pulled forward into Phase 4b (6). Then the HERE per-route corridor feed, and
a Mapbox Traffic Data quote after it, only if measured ETA error justifies them. Then Protomaps tile self-hosting, calendar-derived departure,
live speed into the safety severity term, measured volume profiles.

### Standing risk

Phase 1(3) **as originally written** was the only item on this list. The 2026-09-18
re-scoping removes most of that risk by keeping every floating-point operation in numpy:
1(3a) is `pyref`-only and bitwise-verifiable against a golden hash. 1(3c) was
arithmetic-neutral, so the parity suite was a complete check on it, but it measured
slower and was closed 2026-09-20 without shipping — see ADR-0009's amendment of that
date. Only 1(3b) is genuinely risky, and it is now conditional on a pack size nothing
on this plan calls for. The OpenLR-conflation and traffic-procurement risks were
removed by ADR-0010's deferral.

1(3a) has since landed with its bits pinned, which retires that share of the risk. It
also exposed a gap worth remembering: **the parity suite cannot catch a change to the
shared numpy precompute**, because it proves `pyref` and `sr_core` agree with *each
other* and both consume the same arrays. A pure re-association of the `raw` sum left all
216 other tests green while moving 48 cost arrays. `tests/test_costs_golden.py` is what
closes that hole; treat a change to `pyref/costs.py` with a green parity suite and no
golden digests as unverified.

## Working conventions for this repo

- **TDD.** Tests live in `tests/` (Python) and `web/**/*.test.ts`. Write the
  failing test first; the fixtures in `tests/helpers/` are hand-computable toys.
- **The `/route` contract is frozen.** New wire shapes must be additive, mirrored
  by hand into `web/lib/types.ts`, and mapped in `web/scripts/check-schema-sync.mjs`
  `PAIRS` — the `schema-sync` job fails otherwise. This means a backend contract
  change drags a small `types.ts` edit into the same PR by necessity.
- **Parity core.** `pyref/` (reference) and `sr_core` (C++, optional) are held
  bitwise-identical; `sr_core` is absent locally (tests fall back to pyref).
- **Green bar before a PR:** `.venv/bin/python -m pytest -q`, `.venv/bin/ruff
  check .`, `.venv/bin/python -m mypy`, and (for web changes) `npx tsc --noEmit`,
  `npx eslint`, **and `npm run format:check`** in `web/`. Run mypy bare, with no
  paths and no flags — the `lint` job runs it that way and fails the build, and
  the file list and the deliberate leniency live in `[tool.mypy]` in
  `pyproject.toml`. Neither pytest nor ruff is a substitute: a pydantic
  field-type widening in a subclass passed both and still turned PR #45 red.
  Prettier is not optional either — the `web` CI job runs `format:check` and
  fails the build on a style diff (this bit PR #35). Run `npm run format` to
  auto-fix before committing.

- **Node 22 for `web/`** — what CI uses, pinned in `web/.nvmrc`; `package.json`
  `engines` only demands `>=20.9.0`. Two local traps: under WSL, `/usr/bin/node`
  is v18 and nvm is loaded from `~/.bashrc`, which a non-interactive `bash -lc`
  skips — load nvm from `~/.profile` too or you silently get v18. On the
  Windows side, a `~/.npmrc` with `os=linux` makes `npm ci` skip the win32
  native binaries vitest needs; use `npm ci --os=win32` there.

- **Build `sr_core` locally, or you are not running the parity suite.**
  `tests/test_parity_cpp.py` opens with `pytest.importorskip("sr_core")`, so a
  machine without the built extension reports a green bar having *skipped* the
  thing that proves the Python and C++ cores agree. Measured on one fixed tree:
  **167 passed / 6 skipped without it, 175 passed / 5 skipped with it** — the 8
  skipped tests are the entire parity suite, i.e. the whole bitwise guarantee.
  CI builds it (`pip install ./core`) and enforces it with `SR_CI_STRICT=1`; a
  laptop does not. Before any change to
  `pyref/` that the C++ core mirrors — Phase 1(3) above all — build it:

  ```
  pip wheel --no-deps -w /tmp/wheels ./core   # build isolation supplies pybind11
  pip install /tmp/wheels/*.whl               # or unzip the wheel somewhere on PYTHONPATH
  ```

  Unpacking the wheel onto `PYTHONPATH` rather than installing it keeps the
  extension out of the venv, which is useful when you want to run *both* engines
  from one interpreter to compare them.

- **Building `sr_core` is necessary but not sufficient — check the packs too.**
  There is a *second*, independent way to lose real-pack coverage, and it
  stacks with the one above: `data/` is gitignored, so it exists only in the
  main checkout. A `git worktree` gets a fresh checkout with **no `data/` at
  all**, and the real-pack tests used to name their pack CWD-relatively
  (`"data/packs/berkeley_small"`). So an agent could build the extension, tick
  off the trap above, and still assert nothing against a 20k-edge graph.
  Measured on this tree, with `sr_core` built in both runs:
  **main checkout 228 passed / 5 skipped, worktree 224 passed / 9 skipped** —
  the 4 extra skips were the whole of the real-pack coverage (2 parity, 2
  golden), and the bar was green either way. With seven live worktrees, that
  was the normal case, not an edge case.

  Fixed in `tests/helpers/packs.py`: packs are resolved by **name**, looked for
  under `$SR_PACKS_ROOT`, then `./data/packs`, then the main checkout (via
  `git rev-parse --git-common-dir`, and via an ancestor walk, since worktrees
  live under `<main>/.claude/worktrees/` and the worktree's `.git` file records
  a *Windows* path that `git` inside WSL cannot resolve). Do not reintroduce a
  literal `"data/packs/..."` in a test; use `real_pack("berkeley_small")`.
  `SR_PACKS_ROOT` is **not** `SR_PACK_DIR` — the latter names one pack
  directory and six API tests monkeypatch it to a toy under `tmp_path`.

  When a pack genuinely is missing, `conftest.py` now prints a
  `REDUCED COVERAGE (advisory)` banner after the run naming what was skipped
  and why, including the `sr_core` case above. Locally it is advisory only;
  CI sets `SR_CI_STRICT=1` and `tests/test_ci_preconditions.py` fails instead.

## Refreshing this handoff

The mattpocock `/handoff` skill produces a *conversation* handoff to an OS temp
dir — ephemeral and user-triggered (`disable-model-invocation: true`). Use it for
mid-task context transfer, but the **durable** project plan belongs here, in the
repo. When you finish a feature, tick it off above and add the next one.
