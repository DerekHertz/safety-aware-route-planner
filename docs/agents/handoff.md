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

Robust-core milestone status (ADR-0008), plus the reroute line built on it:

- [x] **Route-artifact v1** — `preference` + `schema_version` (ADR-0004). PR #32.
- [x] **Contract schema-versioned + contract-tested.** `tests/test_route_artifact_v1.py`,
      enforced by the `schema-sync` workflow.
- [x] **Reroute v1** — `POST /reroute`, replan one carried level (ADR-0008). PR #33.
      _Backend + the type-mirror only._
- [~] **Web consumes `/reroute`** — the **client half has landed**: `fetchReroute`
      in `web/lib/api.ts` (tested in `web/lib/api.test.ts`) POSTs the carried
      `preference` and returns the single same-level artifact. It is **not yet
      called by the UI** — the fake reroute in `web/app/page.tsx` (re-calls
      `POST /route` from the new origin and reselects, the silent safety-level
      swap ADR-0008 describes) is still in place. Wiring `fetchReroute` in **is**
      the nav rebuild below. Do NOT bolt it onto the parked nav.
- [ ] **Remove `page.tsx` nav-state overloading; quarantine live nav behind a
      flag** (robust-core milestone item 3, ADR-0008). Largest remaining TS
      feature — **DEFERRED pending more usage/context.** When resumed: on
      `offRoute` while navigating, call `fetchReroute(activeRoute.preference)`
      and **replace the active route in place** (no reselection → same level by
      construction); stop overloading `origin`/`selected`/`followMode` so the
      planner and navigator don't share mutable state; add an arrival lifecycle.
- [x] **Safety scenario suite green** (milestone item 4) — already passing.

After the milestone: nav gets un-parked and rebuilt as a clean consumer
(ADR-0002), at which point the silent-swap bug is gone by construction.

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
