---
Status: accepted
Date: 2026-09-18
---

# The Nominatim budget moves to a shared token bucket; self-hosted Photon is deferred

Geocoding was the reason this service could not run more than one replica. The
Nominatim limiter in `api/geocode.py` was an `asyncio.Lock` plus a module-level
timestamp, so the agreed ~1 request/second was enforced *per process* — and the
README and Dockerfile therefore forbade `--workers` and any replica count above
one. A CPU-bound routing engine was pinned to a single machine by a text-search
side feature.

The limiter now lives in `api/ratelimit.py` as a **token bucket**, backed by
**Redis when `SR_REDIS_URL` is set** and by an in-process bucket otherwise.
Self-hosting a geocoder is **deferred, not rejected**.

## Why a shared bucket rather than self-hosted Photon

Photon removes the rate limit entirely, which is the cleanest possible fix to
the stated problem and is why it was the obvious candidate. It is deferred
because of what it costs to *operate*, not what it costs to adopt: Photon is a
JVM service plus an Elasticsearch-derived index of **several gigabytes per
region**, which must be built, stored, updated on a schedule, memory-sized, and
monitored. That is a genuine second ops surface — a stateful one — standing
next to a stateless container that currently fetches a 2.7 MB pack at startup.
In a one-person project the operator is the same person, and the feature being
served is a search box that finds a street name.

The shared bucket is a few dozen lines against a cache that a multi-replica
deployment will want anyway (rate limiting `POST /route` is the next item on
the plan, and it needs the same store). It does not remove the upstream
dependency — it makes the dependency's ceiling a property of the deployment
instead of a property of one process, which is the specific thing standing
between this service and horizontal scale.

**Revisit when any of these holds:** Nominatim's usage policy is being hit
often enough that 429s are visible to real users rather than absorbed by the
front-end's debounce and the response cache; coverage expands to enough regions
that a per-region index is being built by the ingestion pipeline anyway; or
geocoding becomes load-bearing for something other than a search box — the
commute planner's saved addresses (ADR-0011) would qualify if they were
resolved on every schedule tick rather than once at save time.

## Refusing beats serializing

The old limiter did not throttle at the edge, it **serialized**: over budget,
callers queued on the lock and slept. N concurrent typists each waited N
seconds and each held a connection open to do it. That is worse than refusing
in every dimension — latency, connection count, and honesty — and it made the
geocoder a way to tie up the whole event loop.

Over budget now returns **429 with a `Retry-After`**. The client absorbs it:
`SearchBox.tsx` debounces at 600 ms, the per-process response cache is
consulted before a token is spent, and a dropped keystroke costs a suggestion
list nobody had read yet.

The cache stays **per process** deliberately. Sharing it would mean a Redis
round trip to avoid a Nominatim call the bucket already rations; more replicas
simply means each warms its own and the per-replica hit rate is lower.

## The limiter fails closed

If Redis is configured and unreachable mid-request, `GET /geocode` returns
**503**. It does not serve, and it does not quietly fall back to the in-process
bucket.

This is the opposite of the usual default, and the reason is that this limit is
not protecting our own capacity. It is a **third party's stated usage policy**
that this project is choosing to respect, and the enforcement for breaking it
is a block on our User-Agent — which takes geocoding down for every user, for
an unknown duration, until somebody notices and writes to OSM. Failing open
would breach the policy on every replica simultaneously, at exactly the moment
nobody is watching a dashboard. A Redis outage that costs us search-box
suggestions is recoverable in minutes; a ban is not.

Degrading to the in-process bucket was rejected for being the worst of the
three: it looks safe, and at N replicas it is precisely N times the allowed
rate — silently reinstating the defect this ADR exists to remove.

The blast radius is small by construction. `/route`, `/reroute` and `/meta`
never touch the limiter, so the actual product is unaffected, and the client
can still set origin and destination by map click or GPS.

## Consequences

- **Multiple replicas behind a load balancer are now supported**, and are the
  way to add capacity. `--workers N` is still wrong, for the reason that was
  always the weaker half of the old Dockerfile comment and is now the only
  half: the C++ search releases the GIL and `api/routes.py` is a sync handler,
  so one process already parallelises across every core; extra workers would
  duplicate the graph pack in RAM for no throughput. Removing the geocoder
  constraint did not turn `--workers` into good advice.
- **Without `SR_REDIS_URL`, one replica remains the limit.** The in-process
  bucket is correct and is the right default for local dev and CI, which run
  with no Redis and without the `redis` package installed at all — the import
  in `api/ratelimit.py` is lazy for that reason, and a configured URL with the
  package missing is fatal at startup rather than a silent downgrade.
- The token-bucket arithmetic is a pure function shared by the in-process
  backend and the tests, and **transcribed by hand into Lua** for the Redis
  path. Lua rather than GET/SET because read-modify-write from N replicas is
  the exact race being closed, and it reads the clock from Redis's own `TIME`
  because replicas' clocks drift. That transcription is the one place this
  design can rot; the tests cover the arithmetic, not the Lua, which needs a
  live server.
- Nominatim policy compliance is otherwise unchanged: the identifying
  User-Agent and the `SR_NOMINATIM_CONTACT` override both survive, and the
  ceiling they are attached to is now honest at any replica count.
- This unblocks Phase 1(2), rate-limiting `POST /route`, which wants the same
  shared store and can reuse `RateLimiter` directly.
