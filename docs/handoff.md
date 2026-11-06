# Handoff

What a fresh session needs to continue the MVP build. The next session runs on the owner's
MacBook and builds **S8, the iOS app**. Update the **Status** table whenever a slice lands.
Last updated 2026-11-06.

## Start here on the Mac

1. Clone and check out the working trunk. The repo's default branch, `main`, still holds the old
   tree, so a plain clone lands on the wrong code.

	```bash
	git clone https://github.com/DerekHertz/safety-aware-route-planner.git
	cd safety-aware-route-planner
	git checkout rewrite/mvp-2026-11-05
	```

2. Start the service on recorded data. Either way it serves `http://localhost:8000`, which the iOS
   simulator reaches as `localhost`.

	```bash
	docker compose up --build
	```

	Or, without Docker, with [uv](https://docs.astral.sh/uv/) installed:

	```bash
	cd server && uv sync && uv run uvicorn gapcross.app:app --host 0.0.0.0 --port 8000
	```

3. Check it: `curl localhost:8000/trips` lists one trip, and `http://localhost:8000/docs` shows
   every endpoint. Recorded mode needs no OSRM, Overpass or Jev, and no secrets.
4. Optional: `cd server && uv run pytest` runs the 99 service tests offline.

The Mac session does not need WSL, the OSRM graph (`server/osrm/data/`, built only in WSL) or
`TYPESAFE_API_KEY`. pstack is installed only in WSL, so its skills are not available on the Mac;
`AGENTS.md` and this file carry the method.

## Read first

1. [CONTEXT.md](../CONTEXT.md): the glossary. Use its terms; its `_Avoid_` lists are binding.
2. [docs/design/slices.md](design/slices.md): the S8 row is the job and its check.
3. [docs/design/concrete.md](design/concrete.md): the "iOS follower" section, units, endpoints.
4. [docs/design/properties.md](design/properties.md): rules 11 to 14 apply to the app, word for
   word, and Flow C is the app's state chart.
5. [docs/design/abstract.md](design/abstract.md) (the "App" rules) and
   [ADR-0001](adr/0001-service-and-native-client.md) when a "why" comes up.

The old tree (tag `archive/pre-rewrite-2026-11-05`, `docs/archive/`) is history, not a reference.

## S8: the job

Slice S8 from [slices.md](design/slices.md): an Xcode project in `ios/`; a client generated from
`api/openapi.json` with `swift-openapi-generator`; MapKit routes and markers; the avoidance toggle;
`SimulatedFeed`, `Follower` and the guidance UI in miles and feet.

**Check:** in the simulator, the trip loads, both routes and their markers show, marker counts
match the crossings, and the simulated drive reaches the destination with turns in order. Tests
cover rules 11 to 13.

What the app must do, from the design and the service as built:

- **Contract.** The app reads only `api/openapi.json`, never `server/` code. Generate the client
  from it; do not hand-write request or response types.
- **Planning.** Pick the trip (`GET /trips`), then `POST /trips/{id}/default` and draw it at once.
  With avoidance on, also `POST /trips/{id}/comparison` with `{"avoid": true}` and draw the
  alternative. `comparison` with `{"avoid": false}` returns 422: with avoidance off, call only
  `/default`.
- **Markers** on every crossing of a displayed route: `!` for `flagged`, `?` for `unknown`, a quiet
  check for `unflagged`. Derive counts from `crossings`; the API never sends counts. If any
  crossing is `check_failed`, or the comparison outcome is `check_failed`, show "Crossings couldn't
  be checked" and no markers or counts (rule 2). Each marker opens its `assessment.explanation` and
  evidence; lead evidence (`is_lead`) is shown but never changes anything. See the open question
  below: `/default` cannot yet say that its check failed.
- **Outcome text.** Show `Comparison.summary` as given. `incomplete` and `no_improvement` are
  different messages; never merge them.
- **Labels.** Show the route `label` (`example` for the demo's default) and `source` (`recorded`
  means fixture data). Times read "Estimated drive time" with "Traffic unknown".
- **Units.** The wire carries meters and seconds. Show miles, and feet under about 0.1 mi for turn
  prompts, on screen and in voice.
- **Driving.** `Step.instruction` is ready-made text; `maneuver` gives type and modifier for
  icons. Off route: more than 40 m from the route for more than 5 s (starting guesses). A tap on
  the off-route banner calls `POST /trips/{id}/reroute` with the position; the result is labeled
  `rerouted` and has no crossings.
- **Rule 11:** a rerouted route shows no crossing markers or claims.
- **Rule 12:** a simulated drive never sends a drive report. Only the real feed (S10) sends
  `POST /drive-reports`.
- **Rule 13:** the app never changes the route without the driver's tap.
- **Rule 14:** no app string says "safe". Test every string.
- **Feeds.** `SimulatedFeed(route, speedMultiplier)` owns start, pause and restart, and playback
  speed never changes the ETA. `CoreLocationFeed` is S10. The `Follower` cannot tell them apart.

What the demo should show, from the recorded data:

- Default (example) route, 754 s: 14 crossings. Flagged: Sacramento Street at Oregon, Durant
  Avenue, Bancroft Way. Unknown: Martin Luther King Jr Way at Oregon, the Piedmont roundabout.
- Avoidance on: `improvement`, an alternative via Dohr and Ward Streets, 815 s (61 s more, inside
  the 151 s budget). Sacramento is crossed at Ward, unflagged (signals). Durant and Bancroft stay
  flagged on both routes.

## Status

Trunk is `rewrite/mvp-2026-11-05` on GitHub. Each slice landed as one squash commit from a stacked
PR.

| Slice | State |
|---|---|
| Design, S0 scaffold, wire types, OpenAPI contract | Merged, #127 |
| S1 source seams, trips, recorded fixtures | Merged, #128 |
| S2 movement interpretation (`gapcross.interpret`) | Merged, #129 |
| S3 assessment rules (`gapcross.assess`) | Merged, #130 |
| S4 search and comparison (`gapcross.search`, `gapcross.compare`) | Merged, #131 |
| S5 HTTP service (`gapcross.app`) | Merged, #132 |
| S6 live sources and self-hosted OSRM | Merged, #133. Live `compare()` takes about 0.5 s. |
| S7 Jev adapter, cache, pre-run and gate | Merged, #134. The gate fails (2 of 27 compared, needs 10), so Jev stays a lead. |
| Dependabot for `server/` | Merged, #135. Takes effect once the rewrite reaches `main`. |
| S8 iOS planning and simulated drive | **Next, on the Mac.** |
| S9 deploy to a GCP e2-micro | Not started. Needs a Google Cloud account and project. |
| S10 in-car drive | Not started. Needs S8, S9, the iPhone and an Apple ID for signing. |

## Decisions so far (owner, 2026-11-06)

- **Crossings (S2).** Only `highway` primary, secondary and tertiary make a main street. A turn is
  departure minus approach heading: straight within ±45°, left −45° to −135°, right +45° to
  +135°, beyond is a U-turn. Movements of the same-named main street within 50 m merge into one
  crossing. A left off a divided street is not a crossing.
- **Rules (S3).** With nothing recorded that stops the main street, a stop or yield facing the
  driver is flagged. Stop signs on every approach count as an all-way stop. An undirected sign on
  the junction node is unknown.
- **Rule 3 (S4).** Only the crossings the two routes do not share are compared; see
  properties.md. An alternative crosses on a local street within 3 blocks and rejoins the default
  300 m after the crossing. The alternative's label is `provider`.
- **Service (S5, S6).** The record snapshot is fetched once per corridor at startup. Compose runs
  recorded data by default; `GAPCROSS_SOURCES=live docker compose --profile live up` adds OSRM.
- **Jev (S7).** The model is pinned to `jev-1.14.0`. `approach_control = stops_for_us` means the
  main street's traffic stops for the driver. The gate needs 10 confident agreements.

Open questions for the owner:

1. **`/default` cannot report a failed check.** When the record snapshot cannot be fetched, the
   service returns the default route with an empty `crossings` list, and `Route` has no field that
   says the check failed, so the app would read it as "no crossings" (rule 2). Only
   `Comparison.outcome` carries `check_failed`. Recommended: add a `crossings_checked: bool` to
   `Route` (false on a failed check and on a rerouted route), regenerate the contract, and have the
   app show "Crossings couldn't be checked" when it is false. Settle this before S8 draws markers.
2. Roundabout entries: the two Piedmont passes stay unknown. Recommended: keep them unknown until
   a roundabout rule exists.
3. Adeline becoming Shattuck shows as a crossing of Shattuck. Recommended: accept for the demo.

## How the code is laid out

- `server/` (Python 3.12, `uv`): `uv run pytest` runs everything offline; `-m live` adds the live
  tests, which need the WSL OSRM.
  - `src/gapcross/types.py`: wire types. After changing them, run
    `uv run python -m gapcross.contract` to rewrite `api/openapi.json`; a drift test fails until you do.
  - `sources.py`, `transport.py`: the seams and the live, fixture and recording transports. An
    unrecorded request raises `FixtureMissing`; tests never reach the network.
  - `interpret.py`, `assess.py`, `search.py`, `compare.py`: the engine, S2 to S4.
  - `app.py`: the endpoints. `jev.py`, `prerun.py`: Jev and the corridor gate.
  - `trips.json`: preset trips. The alternative is never listed; the search finds it.
- `api/openapi.json`: the only file the iOS app reads from the service.
- `compose.yaml`: the service on :8000, and `osrm` under the `live` profile.
- `.claude/launch.json`: a `server` preview config that runs uvicorn in WSL (Windows only).

## Working agreements

- **Method** (`AGENTS.md`): each slice starts with a failing test and ends on its check in
  slices.md. At most 5 parallel workers.
- **Rules 3 and 7** are tested against values computed outside the code under test. Do the same
  for rules 11 to 13: observe the app's behavior, not its own flags.
- **No hand-labeling.** Ground truth is the owner's description of the two demo crossings plus
  drive reports.
- **Secrets:** `.env` (gitignored) and the shell environment. Read them by name; never print them.
- **Commits and PRs:** only when the owner says so, on a branch, one commit per slice. PR titles
  and bodies follow pstack's opening-a-pr format: a Conventional Commits title, then `## Why`,
  `## What changed`, `## Scope`, `## Blast Radius` and `## Verification`, in short plain
  sentences. Land stacks bottom-up with squash merges.
- **Questions to the owner:** numbered, each with a recommended answer.

## Known gaps

- `main` still holds the old tree. Moving the rewrite onto `main` is the owner's call.
- `.gitignore` still lists the old `web/` and `core/` paths.
- Vercel is disconnected from the repo; old PRs keep their failed Vercel checks.
- Rerunning the TLA+ check needs `tla2tools.jar` 1.7.4 (sha256 in properties.md).
- Mapillary and street imagery are parked; Jev is text-only for the MVP (ADR-0003).
