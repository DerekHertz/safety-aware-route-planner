# Handoff

What a fresh session needs to continue the MVP build. Update the **Status** table whenever a slice
lands. Last updated 2026-11-06.

## Read first

1. [CONTEXT.md](../CONTEXT.md): the glossary. Use its terms; its `_Avoid_` lists are binding.
2. [docs/design/slices.md](design/slices.md): the build plan, one slice at a time.
3. [docs/design/concrete.md](design/concrete.md): types, seams, endpoints, storage, hosting.
4. [docs/design/properties.md](design/properties.md): the 14 rules, word for word, and which
   test or model checks each.
5. [docs/design/abstract.md](design/abstract.md) and [docs/adr/](adr/) when a "why" comes up.
6. [docs/design/analogy.md](design/analogy.md): the hiking-guide analogy used in every picture and
   grilling round. Rebuild the picture page with the `component-design` skill's
   `scripts/build_pictures.py` after a design change.

The old tree (tag `archive/pre-rewrite-2026-11-05`, `docs/archive/`) is history, not a reference.

## Status

Branch `build/mvp-design-and-s0` (cut from `rewrite/mvp-2026-11-05`). Nothing is pushed.

| Slice | State |
|---|---|
| Design (abstract, concrete, properties, slices) | Committed `6e5b72e` |
| S0 scaffold, wire types, OpenAPI contract | Committed `2712264` |
| S1 source seams, trips, recorded fixtures | Committed `e38413f` (docs in `2d4bc5e`) |
| S2 movement interpretation | Committed (`feat(server): S2 movement interpretation`): `gapcross.interpret`; bbox widened and Overpass re-recorded. Open: roundabout entries and Adeline→Shattuck, for S3. |
| S3 assessment rules | Committed (`feat(server): S3 assessment rules`): `gapcross.assess`; Jev seam is `Advisor`, stubbed by `FixedAdvisor`. |
| S4 search and comparison | Built, tests green (`gapcross.search`, `gapcross.compare`). Candidate routes recorded from the S6 local OSRM with `uv run python -m gapcross.record --search http://localhost:5000`. Rule 3 compares only unshared crossings (properties.md). |
| S5 HTTP service | Committed: `gapcross.app` (`create_app(Service)`, config from `GAPCROSS_*` env), `reroute`, drive reports in `server/var/drive_reports.sqlite`, snapshot kept per corridor, via-point turns announced as turns. |
| S6, S7 | In progress in worktrees `~/tcrf-s6` (branch `build/s6-live-sources`) and `~/tcrf-s7` (branch `build/s7-jev`), uncommitted |
| S8 (iOS, on the Mac) | Not started; needs only `api/openapi.json` |
| S9, S10 | Not started |


### S2 decisions (owner, 2026-11-06)

1. **Unclassified cross streets are local.** Only `highway` primary/secondary/tertiary makes a
   crossing; residential, living_street, service and unclassified are local.
2. **Widen the corridor bbox and re-record.** `trips.json`'s bbox misses Piedmont × Durant and
   Bancroft (tertiary) and campus. Widen it to cover all recorded routes plus ~3 blocks (one
   corridor snapshot, not per route) and re-record Overpass with `gapcross.record`. A junction
   intersection with no OSM node within 3 m is reported as uncovered; S3 makes that
   `check_failed`.
3. **Geometry.** Turn = departure minus approach heading: straight within ±45°, left −45° to
   −135°, right +45° to +135°, beyond is a U-turn. Movements of the same-named main street within
   50 m merge into one crossing (any left makes it `left_onto`). A divided-street group whose
   route arrived on that same street is a left off it, not a crossing.

S2 notes from the recorded data: a via point can sit on a junction (Oregon/Dohr on the Dohr/Ward
route), where OSRM gives no approach bearing, so take headings from the route geometry around
the matched junction node, and count junction degree from OSM ways, not OSRM bearings.
`server/var/explore_s2.py` (gitignored) prints every intersection with its nearest junction.

Tonight's demo target: S0–S5 plus S8.

## How the code is laid out

- `server/` (Python 3.12, `uv`): `uv run pytest` runs everything offline.
  - `src/gapcross/types.py`: wire types. After changing them, run
    `uv run python -m gapcross.contract` to rewrite `api/openapi.json`; a drift test fails until you do.
  - `src/gapcross/sources.py`: the `RouteSource` and `RecordSource` seams, raw types, OSRM and
    Overpass adapters, `offline_sources()` / `live_sources(record=...)`.
  - `src/gapcross/transport.py`: live, fixture and recording transports. Fixtures are keyed by
    service plus request meaning (waypoints, query), not URL. An unrecorded request raises
    `FixtureMissing`; record it with `uv run python -m gapcross.record` (add waypoint lists to
    `fixtures/extra_routes.json`), never let tests reach the network.
  - `trips.json`: preset trips. The alternative is never listed; S4 must find Dohr/Ward itself.
- `api/openapi.json`: the only file the iOS app reads from the service.
- `compose.yaml`: runs the service on :8000 (endpoints return 501 until S5).

## Working agreements

- **Method** (`AGENTS.md`): each slice starts with a failing test (`tdd`) and ends on the check in
  slices.md. At most 5 parallel workers.
- **Rules 3 and 7** must be tested against values computed outside the code under test (an
  independent count, an injected clock). The TLA+ model showed both are easy to check vacuously.
- **No hand-labeling.** Ground truth is the owner's description of the two demo crossings plus
  drive reports; Jev is gated by agreement with the rules.
- **Units:** meters and seconds on the wire; the app shows miles and feet.
- **Secrets:** `.env` (gitignored) holds `MAPBOX_TOKEN`; `TYPESAFE_API_KEY` is in the shell
  environment. Read them by name; never print them.
- **Commits:** only when the owner says so, on a branch, one commit per slice.
- **Questions to the owner:** numbered, each with a recommended answer; close out minor ones with a
  "yes to all" table.
- **Rerunning the TLA+ check** needs `tla2tools.jar` 1.7.4 (sha256 in properties.md); the copy used
  so far was in a session scratchpad and is gone.

## Known gaps

- `.gitignore` still lists the old `web/` and `core/` paths.
- OSRM's public demo server is for recording only; S6 moves the service to a self-hosted
  Berkeley-only extract.
- Mapillary and street imagery are parked; Jev is text-only for the MVP (ADR-0003).
