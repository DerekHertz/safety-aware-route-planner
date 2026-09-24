---
Status: accepted. The store, tester-token and API choices are orchestrator defaults, pending owner review.
Date: 2026-09-24
---

# Trip-trace ingest: a `commute/` service, SQLite, tester tokens, idempotent chunk uploads

ADR-0017 decided that beta **trip traces** go to the commute planner service, and what
they contain. This ADR fixes the shape of that first slice, so the client recorder can be
built against a stable contract. The agent orchestrating the work picked the store, the
token scheme and the API as defaults. Each one is cheap to change until real traces
exist, and the owner has not reviewed them yet.

## Decisions

- **Where.** A new top-level package, `commute/`, served as `uvicorn commute.app:app`. It
  imports nothing from `pyref/`, `sim/`, `core`/`sr_core`, `ingestion/` or `api/`, and
  `tests/test_commute_boundary.py` enforces that (ADR-0011). `api/` is excluded as well:
  the two services share the artifact contract, not code, and `api` loads its config
  through `pyref`.
- **Store (default).** One SQLite file, opened with the stdlib `sqlite3`. The path comes
  from `SR_COMMUTE_DB`, else `data/commute/commute.sqlite3`, which is gitignored. The
  schema is created at startup and versioned in a `schema_version` table. A file with a
  newer version than the code knows is refused. Trace data is **append-only**, and
  triggers enforce it: the one permitted update is the retention purge clearing a body.
  Postgres was rejected for now as a second ops surface before there is a second tester.
- **Tester tokens (default).** The owner mints them offline:
  `python -m commute.tokens issue --label <name>`, plus `list` and `revoke <id>`. The
  plaintext is printed once. Only its SHA-256 is stored, which is sufficient for a random
  256-bit token. Clients send `Authorization: Bearer <token>`. A missing, unknown or
  revoked token gets 401. A trip belongs to the token that first wrote to it, and any
  other token gets 404 for it, which does not confirm that the trip exists.
- **API (default).** Every instant on the wire is an **integer count of epoch
  milliseconds**. That is the unit `GeolocationPosition.timestamp` already uses, and it
  avoids ISO strings.

  | Call | Body | Success |
  |---|---|---|
  | `PUT /v1/trips/{trip_id}/chunks/{seq}` | `TraceChunk` | 201 `ChunkReceipt`, or 200 on an identical replay |
  | `POST /v1/trips/{trip_id}/end` | `TripEnd` | 201 `TripEndReceipt`, or 200 on an identical replay |
  | `GET /v1/me` | none | 200 `TesterInfo`, for checking a token at opt-in |
  | `GET /health` | none | 200, no token needed |

  `trip_id` is a client-generated UUID. `seq` runs from 0 to 99,999. The shapes live in
  `commute/schemas.py` and are mirrored in `web/lib/types.ts`.
- **Idempotent by content.** A chunk is keyed by `(trip, seq)`, and a trip end by its
  trip. The server compares a SHA-256 of the canonical JSON: the parsed body, with keys
  sorted. An identical resend is a 200 no-op. A different body is a 409, and the first
  body is kept. So every retry is safe, in any order. Chunks that arrive after the trip's
  end are still accepted.
- **Hard validation.** Each chunk has at most 600 fixes and 16 artifacts, and at least
  one of either. The body is at most 1 MiB, checked before it is parsed (413). Latitude
  and longitude must be in range. Accuracy and speed must be ≥ 0, and heading must be
  within [0, 360]. `t` must be strictly increasing within a chunk and fall within the
  years 2000–2100, which also catches a timestamp sent in seconds. Numbers must be
  finite. Unknown fields are ignored and not stored, so a newer client still works
  against an older server.
- **One error shape.** Every error body is `{"detail": "<string>"}`. That includes 422,
  flattened from FastAPI's list form into messages like `fixes[3].lat: …`. It names the
  offending field but never echoes the offending value.

## Client obligations (the server cannot check these)

- **The 300 m trim covers artifacts too.** A route artifact's `geometry` runs from the
  trip's origin to its destination. Its `segments`, `maneuvers` and `unsafe_points` sit
  along that line. Uploaded verbatim, it would leak exactly the endpoints ADR-0017 keeps
  on the phone. So before upload, the client clips 300 m off each end of every artifact,
  and drops any maneuver or unsafe point inside the clipped spans. For fixes: none within
  300 m of the origin is uploaded, only fixes at least 300 m behind the latest one are
  flushed, and the held-back tail is discarded at trip end. The server does not re-trim.
  It cannot know the endpoints, and it treats the store as sensitive whether or not the
  trim was perfect.
- **A sealed chunk never changes.** The client assigns `seq` when it seals a chunk into
  IndexedDB, and resends that exact content until it gets a 2xx.
- **Response handling.** Any 2xx is done. 401: stop recording, because the token is dead.
  404, 409, 413 and 422 are permanent, so drop the item and never retry it. 429, 5xx and
  network errors are temporary, so retry on the next flush or launch.
- **Ending a trip.** Flush the last chunk, then send `end`, even for a trip too short to
  upload any fixes. `prediction` comes from the artifact being followed at that moment:
  when it took effect, its `eta_s`, its `preference.level`, and its
  `traffic_basis.profile_version` (null for a v1 artifact).

## Privacy and retention

The store holds the location history of real people. It stays private, it is never
exported, and it is never logged. Nothing logs a request body. The access log records
only the client address, request line and status, and a path holds a trip UUID and a
`seq`, never a place. The raw trace is every chunk body, meaning its fixes and the
artifacts it carried. `python -m commute.retention purge` clears it **90 days after the
server received it**. The server's receipt time is used rather than the phone's clock,
because the phone's clock can be wrong. The store runs with `secure_delete`, so cleared
text does not linger in free pages. What survives the purge carries no location: the trip
row, and each chunk's hash, receipt time, fix count and first and last fix time. Keeping
the hash means a late retry of a purged chunk is still answered as a replay rather than
stored again. The predicted-versus-actual ETA log is kept.

## Consequences

- **The ETA log exists from the first beta drive** (ADR-0011, ADR-0017). Each row
  carries `profile_version`, so after calibration the ETA error can be compared before
  and after each version of the model.
- **One check covers both services.** `scripts/dump_openapi.py` merges this service's
  schemas into the document `schema-sync` checks, so `web/lib/types.ts` is held to both
  services by one mechanism. A schema name the two services define differently fails the
  check.
- **Deployment is a follow-up**: the image, the host, `SR_COMMUTE_CORS_ORIGINS` (or a
  same-origin proxy like the route service's) and a daily purge job. There is no rate
  limit yet, because the tokens are few and known. Add one before any open sign-up.
- **SQLite means one host.** Several uvicorn workers can share the file, because writes
  take `BEGIN IMMEDIATE`, but replicas on different hosts cannot. Moving to Postgres
  would rewrite the store behind an unchanged wire contract.
