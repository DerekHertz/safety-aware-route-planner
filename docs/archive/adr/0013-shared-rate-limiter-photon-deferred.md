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

## Amendment, 2026-09-18 — the routing quota is per client, and it fails open

Phase 1(2) landed on top of this machinery, and it reuses `bucket_step`, both
backends and the connection story unchanged. Everything else about it is the
opposite of the decisions above, because the two limits protect different
things, and reading this ADR without the contrast would be misleading.

Two claims made above are **now false** and are corrected here: "`/route`,
`/reroute` and `/meta` never touch the limiter", and the blast-radius argument
that rests on it. `/route` and `/reroute` now take a token; `/meta`, `/health`
and `/geocode` still do not.

**Per client, not per deployment.** The Nominatim bucket is global because it
rations *our* calls to somebody else's service: a single deployment-wide budget
is literally what the policy describes. Routing is the reverse — the cost is
ours, and the caller is anonymous. A global bucket on the product's core
endpoint would mean the first caller to spend it refuses service to every other
caller, which is a denial of service that needs no volume, only persistence.
So `api/ratelimit.py` grows a `PerClientLimiter`: a bucket per client key, over
either backend, with one Redis key per client under `route_rate_limit_key`
(separate from `rate_limit_key` — welding a search-box budget to a routing
quota would make a burst of typing refuse a trip). The in-process map is an LRU
capped at `route_max_tracked_clients`, because per-client state keyed by a
caller-supplied string is otherwise a memory-exhaustion primitive. LRU rather
than insertion order is a correctness property: an evicted bucket comes back
*full*, so evicting the client currently being throttled would be the bypass.

**What identifies a client, given there is no auth.** Sign-in is Phase 5
(ADR-0011/0012), so the only answer available is the network address, and the
naive forms of that are both wrong. `request.client.host` behind a load
balancer is the balancer, so per-IP collapses into the global bucket this
amendment exists to avoid. `X-Forwarded-For` is *originated by the caller* and
appended to by each proxy, so trusting it lets anyone mint an unlimited number
of identities — each with a fresh full bucket — by varying a header. That is
strictly worse than having no quota at all, because the 429s and the metrics
make it look like one is being enforced.

The only sound reading is to know the hop count. Each proxy appends the address
it received the connection from, so the rightmost entries are the ones our own
infrastructure wrote; with `n` proxies in front, `xff[-n]` is the address the
outermost one actually saw, and any forged prefix sits to its left, inert.
`route_trusted_proxies` (`SR_TRUSTED_PROXIES`) is that count and **defaults to
0, meaning the header is not read at all**. Unparseable values read as 0 too,
so a quoting mistake degrades toward distrust. The default is knowingly blunt
rather than wrong: behind an unconfigured balancer the quota degenerates into
one deployment-wide bucket. That failure is visible and recoverable by setting
one variable; the failure in the other direction is a limiter that silently
does nothing. **Deploying behind a proxy requires setting the hop count** — the
README and config say so at the point of use.

**`/reroute` shares the bucket, one token each.** Both endpoints spend the same
resource — a graph search in the threadpool — and two ceilings would mean the
real ceiling is their sum, which nobody would have written down. A flat token
does over-charge `/reroute` (one search against `/route`'s 3-8); that is
absorbed by sizing for the union rather than by a cost-weighted charge nobody
could reason about. Live navigation is the case that must not break, and the
numbers say it cannot: a successful reroute swaps the followed route's
identity, which resets `useRouteProgress`'s 15 s start-grace and its 3-fix
off-route hysteresis, so the client cannot ask for more than roughly one
reroute per 18 s however lost the traveler is — about 200 tokens an hour
against ~1,800 refilled. `/route` is debounced at 1500 ms in `web/app/page.tsx`,
so a person cannot emit more than ~0.67/s and only while continuously dragging
something. 30/minute with a burst of 15 is therefore unreachable by use and
caps an abuser at roughly four graph searches a second.

**It fails open, which contradicts "The limiter fails closed" above.** That
section is right for what it governs and wrong here, and copying it would have
been the easy mistake. Its argument is that the ceiling enforces a *third
party's* policy whose penalty is a ban on our User-Agent — unrecoverable on our
own timescale, so a short self-inflicted outage is the cheaper error. The
routing quota protects nothing but our own CPU. Failing closed there would turn
a Redis blip into a 503 on the product's core endpoint, on every replica
simultaneously; failing open turns the same blip into unthrottled routing for
its duration, which is a load spike the process survives and a graph shows
afterwards. Given a choice between an outage and a load problem, take the load
problem. (Degrading to the in-process bucket is not the third option it looks
like here either — but for the opposite reason to the geocode case: N replicas
× the per-client quota is a mild overshoot of a limit we set ourselves, so the
distinction barely matters, and "admit" is the simpler thing to reason about.)

**Response bodies are untouched.** A 429 is a new status carrying FastAPI's
standard `{"detail": ...}` — the same shape `/route` already returns for a 422
— plus a `Retry-After`. The wire contract (ADR-0004) is frozen and a new field
would have to be hand-mirrored into `web/lib/types.ts` and
`web/scripts/check-schema-sync.mjs`; there was no reason to spend that.

**Still open.** The quota is per address, so it is per household behind NAT and
per exit node behind a VPN, and it does not survive an attacker with a subnet.
Both are arguments for keying on an account instead, which is exactly what
Phase 5 makes possible; this is the ceiling that holds until then, not the last
word. Nothing here rations *cost* — a request over a future nationwide pack
costs the same one token as one over Berkeley.
