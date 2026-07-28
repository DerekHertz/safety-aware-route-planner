## What and why

<!-- The change, and the problem it solves. -->

## Checks that CI cannot make for you

Delete what doesn't apply.

- [ ] **Touched `pyref/costs.py`, `pyref/search.py`, or `core/src/`?** The parity
      suite is green on **both** Linux and Windows. If it went red, the fix is
      the compiler flags in `core/setup.py` — never `SR_PARITY_LOOSE=1`, which
      exists for local diagnosis and would defeat the point of the check.
- [ ] **Touched `api/schemas.py`?** `web/lib/types.ts` moved with it, and
      `tests/test_api_contract.py` was updated deliberately. Note `POST /route`
      is documented as frozen: additive optional request fields are the
      established precedent (`detour_budget_pct`), response changes are not.
- [ ] **Changed `[region.presets]`, `INGEST_SCHEMA_VERSION`, or
      `PACK_FORMAT_VERSION`?** Re-ran `build-packs` under a **new tag** and
      updated `packs.lock`. Published tags are immutable — overwriting one
      breaks every pinned checkout. Remember digests can only come from the run
      that published; they cannot be re-derived by rebuilding, because
      `ingestion/pack.py` stamps `created_utc` into each manifest.
- [ ] **Changed the cost model or `config/config.toml` weights?** Checked the
      effect on a real route, not just the toy fixtures — the defaults are tuned
      so a safe route reaches zero counted unsafe maneuvers within the detour
      budget.
- [ ] **Changed the web build pipeline?** `copy:maplibre-worker` still runs. A
      missed copy is a silently blank map with no error anywhere;
      `scripts/assert-build-assets.mjs` is the guard, don't route around it.
- [ ] **Added a dependency?** It went in the right file — `requirements.txt` is
      the runtime/Docker layer and deliberately excludes osmnx and its geo
      stack.

## Verification

<!-- What you actually ran, and what it printed. Not "tests pass". -->
