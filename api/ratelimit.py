"""Token buckets for the two ceilings this service enforces.

The first is the upstream Nominatim budget — one bucket for the whole
deployment. The second is the routing quota on `POST /route` / `POST /reroute`
— one bucket *per client*. They share `bucket_step`, both backends and the
Redis connection story, and they are deliberately different shapes; the
`PerClientLimiter` docstring below says why, and ADR-0013 (as amended) records
it.

Nominatim's usage policy allows roughly one request per second for this
application *in total*. That is a statement about the deployment, not about a
process, so enforcing it with module-level state is only correct while there is
exactly one process — which is why the Dockerfile and README used to forbid
scaling out at all.

Two things changed here.

**The budget can be shared.** Point `SR_REDIS_URL` at a Redis and every replica
draws from one bucket, so the ceiling holds no matter how many replicas are
behind the load balancer. With no URL configured the bucket stays in-process,
which is still correct at one replica and keeps local dev, CI and the test
suite running with no Redis and without the `redis` package installed at all —
hence the lazy import in `connect()`.

**Over budget is a refusal, not a queue.** The previous implementation was an
`asyncio.Lock` plus monotonic spacing: it did not throttle, it *serialized*, so
N concurrent typists each waited N seconds and every one of them held a
connection open for it. A 429 with a `Retry-After` is strictly better — the
front-end already debounces at 600 ms and the response cache absorbs repeats,
so a refused keystroke costs a suggestion nobody was going to read, while a
queued one costs a request slot.
"""
from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from typing import Protocol

from pyref.config import Config


class LimiterUnavailable(RuntimeError):
    """The limiter could not give an authoritative answer.

    Raised only by the shared backend, and only when Redis is configured and
    unreachable. See the fail-closed note on `RedisTokenBucket.acquire`.
    """


class RateLimiter(Protocol):
    async def acquire(self) -> float | None:
        """Take one token. `None` when admitted, else seconds until the next
        token is available (the value a `Retry-After` is derived from)."""

    async def aclose(self) -> None:
        ...


def retry_after_header(seconds: float) -> str:
    """Render a `retry_after` as an RFC 9110 delta-seconds header value.

    An integer, and never 0: `Retry-After: 0` invites an immediate retry that
    is guaranteed to be refused again, which is how a polite client turns into
    a hot loop. Ceiling rather than round for the same reason.
    """
    return str(max(1, math.ceil(seconds)))


def bucket_step(tokens: float, ts: float, now: float, rate: float,
                capacity: float) -> tuple[float, float, float | None]:
    """Advance a bucket to `now` and try to take one token.

    Returns `(tokens, ts, retry_after)`, with `retry_after` None when the take
    succeeded. Pure and side-effect free on purpose: it is the single
    definition of the arithmetic, shared by the in-process backend, the tests,
    and — transcribed by hand — the Lua in `_TAKE_TOKEN`. Keep those in step.

    `max(0.0, ...)` on the elapsed term is not defensive noise. Redis's clock
    is wall time and an NTP step can move it backwards; a negative elapsed
    would otherwise *destroy* tokens (and, with a negative `rate` term in the
    other direction, credit them). Zero elapsed is the only honest reading of
    a clock that went backwards.
    """
    tokens = min(capacity, tokens + max(0.0, now - ts) * rate)
    if tokens >= 1.0:
        return tokens - 1.0, now, None
    return tokens, now, (1.0 - tokens) / rate


class InProcessTokenBucket:
    """The fallback: one bucket per process.

    Correct at exactly one replica. At N replicas it permits N times the agreed
    rate, which is the defect this module exists to fix, so the README ties
    "more than one replica" to configuring Redis rather than to a vague warning.

    No lock: `acquire` contains no `await`, so the read-modify-write cannot be
    interleaved by the event loop, and the endpoint is the only caller.
    """

    def __init__(self, rate: float, capacity: float, *, clock=time.monotonic):
        self.rate = rate
        self.capacity = capacity
        self._clock = clock
        self._tokens = float(capacity)
        self._ts = clock()

    async def acquire(self) -> float | None:
        self._tokens, self._ts, retry = bucket_step(
            self._tokens, self._ts, self._clock(), self.rate, self.capacity)
        return retry

    async def aclose(self) -> None:
        pass


# Lua, not GET/SET from Python, because read-modify-write from N replicas is
# exactly the race this is meant to close — a script runs atomically on the
# server, so two replicas can never both see the last token.
#
# The clock is Redis's own `TIME`, not the caller's: replicas' clocks drift,
# and a shared bucket read against N different clocks is N buckets wearing a
# hat. `TIME` is non-deterministic but has been legal in scripts since Redis 5
# replicates script *effects* rather than the script itself.
#
# `retry_after` comes back as a string because Redis converts a Lua number to
# an integer on the way out, which would truncate every sub-second wait to 0
# and invite an immediate retry that is guaranteed to fail.
#
# This is a hand transcription of `bucket_step` above. Change both or neither.
_TAKE_TOKEN = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
local retry_after = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_after = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
-- An untouched bucket refills to full after capacity/rate seconds, after which
-- the key carries no information. Expiring it keeps an abandoned deployment's
-- keys from living in Redis forever.
redis.call('PEXPIRE', KEYS[1], math.ceil((capacity / rate) * 2000))
return {allowed, tostring(retry_after)}
"""


class RedisTokenBucket:
    """One bucket for the whole deployment, keyed in Redis."""

    def __init__(self, client, key: str, rate: float, capacity: float):
        self._client = client
        self.key = key
        self.rate = rate
        self.capacity = capacity

    async def acquire(self) -> float | None:
        """Take a token, or raise `LimiterUnavailable` if Redis cannot answer.

        **This fails closed, and that is deliberate.** The three options when a
        configured Redis is unreachable mid-request are:

        *Fail open* (serve anyway) — every replica then serves unthrottled at
        once. This limit is not protection for our own capacity, where a brief
        overshoot is merely load; it is a third party's stated usage policy,
        and the enforcement for breaking it is a block on this project's
        User-Agent. That takes geocoding down for every user until somebody
        notices and writes to OSM. A short outage is cheaper than a ban.

        *Degrade to the in-process bucket* — the worst option, because it looks
        safe. At N replicas it is precisely N times the allowed rate, i.e. it
        silently reinstates the bug this module was written to remove, at the
        moment nobody is watching.

        *Fail closed* (this) — geocoding returns 503 while Redis is down. The
        blast radius is the search box's suggestions; `/route`, `/reroute` and
        `/meta` never touch the limiter, so the actual product keeps working,
        and the front-end already falls back to map clicks and GPS for
        choosing points. See ADR-0013.
        """
        try:
            allowed, retry_after = await self._client.eval(
                _TAKE_TOKEN, 1, self.key, self.rate, self.capacity)
        except Exception as exc:
            # Deliberately broad: redis-py's exception classes cannot be named
            # here without importing redis at module scope, which is the thing
            # the optional dependency forbids. Any failure to get an
            # authoritative answer is the same condition regardless of type.
            raise LimiterUnavailable(str(exc)) from exc
        return None if int(allowed) == 1 else float(retry_after)

    async def aclose(self) -> None:
        await self._client.aclose()


# --- who is the client? ------------------------------------------------------
#
# The routing quota is per client, which forces a question the global Nominatim
# bucket never had to answer: what *is* a client, on an endpoint with no auth?
# (Sign-in is Phase 5 — ADR-0011/0012.) Until there is an account to key on, the
# only answer available is the network address, and getting it from the network
# address is the part that goes wrong quietly.

_UNKNOWN_CLIENT = "unknown"


def trusted_proxies(cfg: Config) -> int:
    """How many proxies sit in front of this process. SR_TRUSTED_PROXIES wins.

    Environment first, like SR_REDIS_URL and SR_CORS_ORIGINS: the hop count is
    a property of where the container is deployed, not of the code.

    Anything unparseable reads as 0. That is not sloppiness — 0 is the
    *distrusting* value, so a shell-quoting mistake or a stray unit ("1 proxy")
    degrades toward ignoring a caller-supplied header rather than toward
    believing one.
    """
    raw = (os.environ.get("SR_TRUSTED_PROXIES") or "").strip()
    if not raw:
        raw = str(cfg["api"]["route_trusted_proxies"]).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def client_key(peer: str | None, forwarded_for: str | None, hops: int) -> str:
    """Identify the caller: the peer address, or an `X-Forwarded-For` entry
    vouched for by a proxy we actually run.

    Pure, and taking strings rather than a `Request`, for the same reason
    `bucket_step` is: this is the trust boundary, and it should be arguable
    about — and testable — without an ASGI stack in the way.

    **The trap.** Behind a load balancer every `request.client.host` is the
    balancer, so keying on it collapses every caller into one bucket and hands
    any single client a veto over everyone else's routing. The usual fix is to
    read `X-Forwarded-For`, but that header is *appended to by each proxy and
    originated by the caller*: a request can arrive carrying an entirely forged
    one. Reading it left-to-right, or trusting all of it, means an attacker
    mints a fresh identity — and a fresh full bucket — per request, by varying
    a string. That is strictly worse than no quota, because the metrics and the
    429s make it look like one is being enforced.

    **The fix.** Each proxy appends the address it received the connection
    from, so the *rightmost* entries are the ones infrastructure wrote and the
    leftmost are whatever the caller sent. With `hops` proxies in front of us,
    the last `hops` entries are ours to trust and `xff[-hops]` is the address
    our outermost proxy actually saw. A forged prefix sits to the left of that
    and is inert. With `hops == 0` — the default — the header is never read.

    A header with fewer entries than `hops` did not traverse the configured
    proxies (a direct hit on the container, or a wrong count), so there is
    nothing trustworthy in it and the peer address is used instead. A request
    with no peer at all keys to a shared `unknown` bucket: one crowded bucket
    is a fair-use problem, whereas raising here would be a crash in the code
    whose job is to survive abuse.
    """
    peer = (peer or "").strip()
    if hops > 0 and forwarded_for:
        chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return peer or _UNKNOWN_CLIENT


class PerClientLimiter:
    """One token bucket per client, over the same backends as the global one.

    The Nominatim limiter is a single bucket because it rations *our* calls to
    a third party: one deployment-wide budget is exactly the thing the policy
    describes. Routing is the opposite. The cost is ours, the caller is
    anonymous, and a single bucket would mean the first client to spend it
    refuses service to every other client — a denial of service that needs no
    volume, only persistence. So the quota is per client and the ceiling is
    "what one caller may do", not "what the deployment may do".

    `make_bucket` builds the backend for a key, so this class stays ignorant of
    which one it has. With Redis that is a stateless shell around one key per
    client, expired by the Lua's `PEXPIRE`; in process it is real state, which
    is why `max_clients` exists.

    Eviction is LRU rather than insertion order, and that is a correctness
    property, not tidiness: a dropped bucket comes back *full*, so evicting the
    client currently being throttled would be a bypass — cycle `max_clients`
    identities and your own limit resets. Least-recently-used makes the caller
    under active refusal the last one dropped, and makes the bypass cost
    `max_clients` requests to buy back one burst.

    No lock, for the same reason `InProcessTokenBucket` needs none: there is no
    `await` between reading the map and writing it, so the event loop cannot
    interleave two callers into one entry.
    """

    def __init__(self, make_bucket: Callable[[str], RateLimiter], *,
                 max_clients: int = 4096, client=None):
        self._make = make_bucket
        self._max = max(1, max_clients)
        self._buckets: dict[str, RateLimiter] = {}
        # The Redis connection, when there is one, so `aclose` can hand back
        # the pool. None for the in-process backend, where it is a no-op.
        self._client = client

    @property
    def tracked(self) -> int:
        return len(self._buckets)

    def bucket_for(self, key: str) -> RateLimiter | None:
        """The backend currently serving `key`, or None. For tests and
        diagnostics — the endpoint only ever calls `acquire`."""
        return self._buckets.get(key)

    async def acquire(self, key: str) -> float | None:
        bucket = self._buckets.pop(key, None)
        if bucket is None:
            if len(self._buckets) >= self._max:
                # Oldest touched first: `dict` preserves insertion order and the
                # pop/reinsert above keeps "insertion order" meaning "recency".
                self._buckets.pop(next(iter(self._buckets)))
            bucket = self._make(key)
        self._buckets[key] = bucket
        return await bucket.acquire()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def redis_url(cfg: Config) -> str:
    """SR_REDIS_URL, else `[api].redis_url`, else empty (in-process).

    Environment first, like SR_CORS_ORIGINS and SR_PACK_DIR: the address of a
    cache is a deployment fact, often carries a password, and is not knowable
    at commit time. An empty or whitespace-only env var falls back rather than
    half-configuring.
    """
    env = (os.environ.get("SR_REDIS_URL") or "").strip()
    return env or str(cfg["api"]["redis_url"]).strip()


def rate_limit_key(cfg: Config) -> str:
    """The Redis key the bucket lives at. SR_RATE_LIMIT_KEY overrides.

    Overridable because staging and production may share one Redis instance,
    and sharing the *key* would mean staging eating production's budget.
    """
    return os.environ.get("SR_RATE_LIMIT_KEY") or str(cfg["api"]["rate_limit_key"])


def route_rate_limit_key(cfg: Config) -> str:
    """Prefix for the per-client routing keys. SR_ROUTE_RATE_LIMIT_KEY overrides.

    Separate from `rate_limit_key` on purpose. The search box's upstream budget
    and the router's own-CPU quota are unrelated ceilings that happen to share a
    Redis; welding them to one key would make a burst of typing refuse a route.
    """
    return (os.environ.get("SR_ROUTE_RATE_LIMIT_KEY")
            or str(cfg["api"]["route_rate_limit_key"]))


def connect(url: str):
    """Build the async Redis client, importing `redis` lazily.

    The import is here rather than at module scope because local dev, CI and
    the entire test suite run with no Redis and without the package installed;
    a top-level import would make a side feature's limiter a hard requirement
    for starting the app.

    A configured URL with no package is fatal, not a warning: silently falling
    back to the per-process bucket is exactly the failure this module removes,
    and it would show up only as a complaint from OSM weeks later.
    """
    try:
        import redis.asyncio as redis_asyncio
    except ImportError as exc:
        raise RuntimeError(
            "A shared rate limiter is configured (SR_REDIS_URL / [api].redis_url) "
            "but the `redis` package is not installed. Refusing to start rather "
            "than fall back to the per-process limiters, which at N replicas "
            "are N times every ceiling this module enforces — Nominatim's "
            "stated policy, and the per-client routing quota alike."
        ) from exc
    return redis_asyncio.from_url(url)


def build_limiter(cfg: Config, *, client=None) -> RateLimiter:
    """Pick a backend from config. `client` injects a pre-built Redis client
    (the tests use it to drive the shared path with no server)."""
    api = cfg["api"]
    rate = 1.0 / float(api["nominatim_min_interval_s"])
    capacity = float(api["nominatim_burst"])
    if client is None:
        url = redis_url(cfg)
        if not url:
            return InProcessTokenBucket(rate, capacity)
        client = connect(url)
    return RedisTokenBucket(client, rate_limit_key(cfg), rate, capacity)


def build_route_limiter(cfg: Config, *, client=None) -> PerClientLimiter:
    """The per-client routing quota, on whichever backend is configured.

    Deliberately its own Redis connection rather than the geocode limiter's.
    redis-py's pools are lazy, so an unused one costs nothing, and the
    alternative — one client owned by two limiters — makes `aclose` a question
    of who goes second. Each limiter closes what it opened.
    """
    api = cfg["api"]
    rate = float(api["route_rate_per_s"])
    capacity = float(api["route_burst"])
    max_clients = int(api["route_max_tracked_clients"])
    if client is None:
        url = redis_url(cfg)
        if url:
            client = connect(url)
    if client is None:
        return PerClientLimiter(
            lambda key: InProcessTokenBucket(rate, capacity),
            max_clients=max_clients)
    prefix = route_rate_limit_key(cfg)
    redis_client = client
    return PerClientLimiter(
        lambda key: RedisTokenBucket(redis_client, f"{prefix}:{key}", rate, capacity),
        max_clients=max_clients, client=redis_client)
