"""The upstream Nominatim budget, as a token bucket that can live outside the process.

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

import os
import time
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
            "than fall back to the per-process limiter, which would exceed "
            "Nominatim's rate policy once per replica."
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
