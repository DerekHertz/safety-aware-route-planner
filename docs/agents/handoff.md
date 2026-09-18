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

- [ ] **(1) Decouple geocoding from the process model.** Self-hosted Photon, or move the
      limiter to Redis. Today `api/geocode.py` funnels every user through one global
      ~1 req/s lock, which does not throttle at the edge -- it *serializes*, so N
      concurrent typists wait N seconds. It is also why the README and Dockerfile mandate
      a single process with no `--workers`, pinning a CPU-bound engine to one replica for
      a text-search side feature. Delete those constraints as part of this. **First code
      PR; everything about scale is downstream of it.**
- [ ] **(2) Rate-limit `POST /route`.** Currently unauthenticated, no quota, 3-8 graph
      searches per call, one process. Should not wait for users to exist.
- [ ] **(3) Lazy per-turn cost evaluation.** `pyref/costs.py` materializes full-graph
      arrays per request (and `arc_cost` again per lambda and per rerun). Evaluate over
      the explored frontier instead. **`pyref` and `sr_core` must change together and stay
      bitwise identical**, so this is one large PR, not two. See ADR-0009's amendment.

**Phase 2 - cheap wins, parallel to Phase 1.**

- [ ] Fast-vs-safe comparison UI. **Zero backend**: one `/route` response already carries
      all three alternatives with `UnsafeCounts`, per-segment tiers and `detour_pct`.
      Best effort-to-value ratio in the plan.
- [x] `busy_floor_by_class` + a scenario test pinning the 3am arterial (ADR-0005).
- [ ] Screen Wake Lock, with `visibilitychange` reacquisition (ADR-0012).

**Phase 3 - contract, then nav.**

- [ ] `traffic_basis` in `preference`, `schema_version` -> 2. Do it **before** the commute
      service exists: a contract change is cheapest while there is one consumer. Drags the
      hand-mirrored `web/lib/types.ts` edit and a `check-schema-sync.mjs` `PAIRS` entry
      into the same PR.
- [ ] Promote live nav off `NEXT_PUBLIC_ENABLE_LIVE_NAV` (ADR-0008's resolution: wake lock
      + one real field drive).

**Phase 4 - multi-metro.** Pack-per-metro selection and routing. Gated on Phase 1(3).

**Phase 5 - commute planner** (ADR-0011). Google Sign-In, accounts, saved commutes with
user-set departure times, the **departure-time sweep** as the headline feature, a stubbed
disruption-source interface, a daily brief, and predicted-vs-actual ETA logging.

**Phase 6 - trigger-gated** (see ADR-0010's triggers). Free event feeds first (511.org,
WZDx, PeMS pending its access terms), then a paid speed layer only if the Phase 5
measurements justify it. Then Protomaps tile self-hosting, calendar-derived departure,
live speed into the safety severity term, measured volume profiles.

### Standing risk

Phase 1(3) is a large PR that must hold Python/C++ parity. It is the only item on the
plan's risk list; the OpenLR-conflation and traffic-procurement risks were removed by
ADR-0010's deferral.

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
  check .`, and (for web changes) `npx tsc --noEmit`, `npx eslint`, **and
  `npm run format:check`** in `web/`. Prettier is not optional — the `web` CI
  job runs `format:check` and fails the build on a style diff (this bit PR #35).
  Run `npm run format` to auto-fix before committing.

## Refreshing this handoff

The mattpocock `/handoff` skill produces a *conversation* handoff to an OS temp
dir — ephemeral and user-triggered (`disable-model-invocation: true`). Use it for
mid-task context transfer, but the **durable** project plan belongs here, in the
repo. When you finish a feature, tick it off above and add the next one.
