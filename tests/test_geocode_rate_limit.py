"""The Nominatim budget is a token bucket, and it can live outside the process.

Two properties are under test, and they are the whole point of the change:

  * **Over budget is a refusal, not a queue.** The old limiter was an
    `asyncio.Lock` plus monotonic spacing, so N concurrent typists waited N
    seconds each. A 429 with a `Retry-After` is strictly better: the client's
    debounce and the response cache absorb it, and nobody's request is held
    open behind someone else's.
  * **The ceiling holds across replicas.** With a Redis-backed bucket the
    ~1 req/s ceiling is a property of the deployment, not of one process, which
    is what makes running more than one replica legal at all (ADR-0013).

Redis is deliberately NOT required to run this file. The Redis path is tested
by injecting a fake client that executes the same pure arithmetic the Lua
script transcribes; two clients sharing one store model two replicas
contending on one key. The Lua itself can only be exercised against a live
server, and is not covered here.
"""
from __future__ import annotations

import asyncio
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from api.ratelimit import (
    InProcessTokenBucket,
    LimiterUnavailable,
    RedisTokenBucket,
    bucket_step,
    build_limiter,
    connect,
    rate_limit_key,
    redis_url,
)
from pyref.config import DEFAULT_CONFIG_PATH, Config
from tests.helpers.fixtures import unprotected_left_city


def run(coro):
    """Drive one coroutine to completion.

    The suite has no async-test plugin and does not need one for a handful of
    coroutines whose bodies never actually await anything slow.
    """
    return asyncio.run(coro)


class FakeClock:
    """Hand-cranked monotonic clock. Rate limiting is arithmetic on elapsed
    time, and a test that has to actually sleep 1.1 s to observe a refill is a
    test nobody will keep running."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRedisClient:
    """Implements the single call `RedisTokenBucket` makes — `EVAL` of the
    token-bucket script — by running `bucket_step`, the same pure function the
    Lua transcribes.

    `store` is shared to model replicas: two clients over one dict are two API
    processes contending on one Redis key. Values come back as bytes because
    that is what redis-py returns from a script.
    """

    def __init__(self, store: dict, clock: FakeClock, fail: bool = False):
        self.store = store
        self.clock = clock
        self.fail = fail
        self.calls = 0

    async def eval(self, script, numkeys, key, rate, capacity):
        self.calls += 1
        if self.fail:
            raise ConnectionError("connection refused")
        rate, capacity = float(rate), float(capacity)
        tokens, ts = self.store.get(key, (capacity, self.clock()))
        tokens, ts, retry = bucket_step(tokens, ts, self.clock(), rate, capacity)
        self.store[key] = (tokens, ts)
        if retry is None:
            return [1, b"0"]
        return [0, str(retry).encode()]

    async def aclose(self):
        pass


class TestBucketArithmetic:
    """`bucket_step` is the one definition of the arithmetic; both backends and
    the Lua script are thin shells around it."""

    def test_a_full_bucket_admits_and_spends_one_token(self):
        tokens, ts, retry = bucket_step(1.0, 100.0, 100.0, rate=1.0, capacity=1.0)
        assert retry is None
        assert tokens == 0.0
        assert ts == 100.0

    def test_an_empty_bucket_refuses_and_says_how_long_to_wait(self):
        tokens, _ts, retry = bucket_step(0.0, 100.0, 100.0, rate=1 / 1.1, capacity=1.0)
        assert retry == pytest.approx(1.1)
        assert tokens == 0.0  # a refusal must not spend anything

    def test_tokens_refill_at_the_configured_rate(self):
        tokens, _ts, retry = bucket_step(0.0, 100.0, 100.55, rate=1 / 1.1, capacity=1.0)
        assert retry == pytest.approx(0.55)
        # half an interval of waiting buys half a token, not a whole one
        assert tokens == pytest.approx(0.5)

    def test_idling_does_not_bank_an_unbounded_burst(self):
        """The cap is the reason `nominatim_burst` exists as a named knob: an
        hour of silence must not entitle a deployment to 3,000 requests."""
        tokens, _ts, retry = bucket_step(0.0, 100.0, 3700.0, rate=1.0, capacity=2.0)
        assert retry is None
        assert tokens == 1.0  # capacity 2, one spent — not 3600

    def test_a_clock_that_goes_backwards_does_not_create_tokens(self):
        """`time.monotonic()` is per-process and Redis's `TIME` is wall clock,
        so a negative elapsed is possible on an NTP step. It must read as zero
        elapsed, never as a credit."""
        tokens, ts, retry = bucket_step(0.0, 100.0, 90.0, rate=1.0, capacity=1.0)
        assert retry == pytest.approx(1.0)
        assert tokens == 0.0
        assert ts == 90.0


class TestInProcessTokenBucket:
    """The fallback backend. Same arithmetic, state in a module-level object —
    correct at exactly one replica, which is what the README now says."""

    def test_refuses_the_second_caller_instead_of_queueing_it(self):
        clock = FakeClock()
        bucket = InProcessTokenBucket(rate=1 / 1.1, capacity=1.0, clock=clock)
        assert run(bucket.acquire()) is None
        # The old limiter would have slept 1.1 s here and then served. That is
        # the behaviour being deleted: a refusal the client can absorb beats a
        # request held open.
        assert run(bucket.acquire()) == pytest.approx(1.1)
        assert clock.now == 1000.0  # nothing slept

    def test_admits_again_once_the_interval_has_passed(self):
        clock = FakeClock()
        bucket = InProcessTokenBucket(rate=1 / 1.1, capacity=1.0, clock=clock)
        run(bucket.acquire())
        clock.advance(1.1)
        assert run(bucket.acquire()) is None

    def test_two_buckets_do_not_share_a_budget(self):
        """Stated as a test because it is the whole defect: two processes are
        two budgets, i.e. twice the agreed request rate upstream."""
        clock = FakeClock()
        a = InProcessTokenBucket(rate=1 / 1.1, capacity=1.0, clock=clock)
        b = InProcessTokenBucket(rate=1 / 1.1, capacity=1.0, clock=clock)
        assert run(a.acquire()) is None
        assert run(b.acquire()) is None


class TestRedisTokenBucket:
    def test_one_shared_bucket_caps_every_replica_together(self):
        """The property the whole change exists for: two API processes, one
        budget. Replica B is refused because replica A already spent it."""
        clock, store = FakeClock(), {}
        replica_a = RedisTokenBucket(
            FakeRedisClient(store, clock), key="k", rate=1 / 1.1, capacity=1.0)
        replica_b = RedisTokenBucket(
            FakeRedisClient(store, clock), key="k", rate=1 / 1.1, capacity=1.0)
        assert run(replica_a.acquire()) is None
        assert run(replica_b.acquire()) == pytest.approx(1.1)
        clock.advance(1.1)
        assert run(replica_b.acquire()) is None

    def test_distinct_keys_are_distinct_budgets(self):
        """Two deployments may share one Redis; `rate_limit_key` is what keeps
        staging from eating production's budget."""
        clock, store = FakeClock(), {}
        prod = RedisTokenBucket(
            FakeRedisClient(store, clock), key="prod", rate=1.0, capacity=1.0)
        staging = RedisTokenBucket(
            FakeRedisClient(store, clock), key="staging", rate=1.0, capacity=1.0)
        assert run(prod.acquire()) is None
        assert run(staging.acquire()) is None

    def test_an_unreachable_redis_raises_rather_than_serving(self):
        """Fail closed. Serving on a limiter failure would exceed a third
        party's stated policy across every replica at once — see ADR-0013."""
        clock = FakeClock()
        bucket = RedisTokenBucket(
            FakeRedisClient({}, clock, fail=True), key="k", rate=1.0, capacity=1.0)
        with pytest.raises(LimiterUnavailable):
            run(bucket.acquire())


class TestBackendSelection:
    def test_no_url_configured_means_the_in_process_bucket(self, monkeypatch):
        monkeypatch.delenv("SR_REDIS_URL", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert redis_url(cfg) == ""
        assert isinstance(build_limiter(cfg), InProcessTokenBucket)

    def test_env_supplies_the_url_the_committed_config_cannot(self, monkeypatch):
        """Same shape as SR_CORS_ORIGINS and SR_PACK_DIR: the value is a
        deployment fact, so the file ships empty and the environment fills it."""
        monkeypatch.setenv("SR_REDIS_URL", " redis://cache:6379/0 ")
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert redis_url(cfg) == "redis://cache:6379/0"

    def test_an_empty_env_var_falls_back_rather_than_half_configuring(self, monkeypatch):
        monkeypatch.setenv("SR_REDIS_URL", "")
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert redis_url(cfg) == cfg["api"]["redis_url"]

    def test_key_comes_from_config_and_the_environment_overrides_it(self, monkeypatch):
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        monkeypatch.delenv("SR_RATE_LIMIT_KEY", raising=False)
        assert rate_limit_key(cfg) == cfg["api"]["rate_limit_key"]
        monkeypatch.setenv("SR_RATE_LIMIT_KEY", "sr:staging:nominatim")
        assert rate_limit_key(cfg) == "sr:staging:nominatim"

    def test_an_injected_client_selects_the_redis_bucket(self, monkeypatch):
        monkeypatch.delenv("SR_REDIS_URL", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        limiter = build_limiter(cfg, client=FakeRedisClient({}, FakeClock()))
        assert isinstance(limiter, RedisTokenBucket)

    def test_the_bucket_is_built_from_the_configured_interval_and_burst(self, monkeypatch):
        monkeypatch.delenv("SR_REDIS_URL", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        limiter = build_limiter(cfg)
        assert limiter.rate == pytest.approx(1.0 / cfg["api"]["nominatim_min_interval_s"])
        assert limiter.capacity == float(cfg["api"]["nominatim_burst"])

    def test_configured_redis_with_no_redis_package_fails_loudly(self, monkeypatch):
        """Configuring a shared limiter and then quietly falling back to the
        per-process one would reinstate the exact bug this removes, so the
        missing import is fatal at startup rather than a warning."""
        monkeypatch.setitem(sys.modules, "redis", None)
        monkeypatch.setitem(sys.modules, "redis.asyncio", None)
        with pytest.raises(RuntimeError, match="redis"):
            connect("redis://cache:6379/0")


# --- the endpoint ----------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Stand-in for `httpx.AsyncClient`. Records what was sent upstream so the
    User-Agent obligation can be asserted, and counts calls so a cache hit
    proving it did NOT go upstream is observable."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        FakeAsyncClient.calls.append({"url": url, "params": params,
                                      "headers": headers})
        return FakeResponse([{"display_name": "Somewhere", "lat": "37.87",
                              "lon": "-122.27"}])


@pytest.fixture()
def client(tmp_path, monkeypatch):
    pack, _ids = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    monkeypatch.delenv("SR_REDIS_URL", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.calls = []

    # Module-level, so it survives between tests and would otherwise let one
    # test's query answer another's from cache.
    from api import geocode as geocode_module
    geocode_module._cache.clear()

    from api.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c
    geocode_module._cache.clear()


def _pin_limiter(client, capacity=1.0):
    """Swap in a hand-cranked clock so the test controls refills."""
    clock = FakeClock()
    client.app.state.app_state.limiter = InProcessTokenBucket(
        rate=1 / 1.1, capacity=capacity, clock=clock)
    return clock


class TestGeocodeEndpoint:
    def test_over_budget_is_429_with_a_retry_after(self, client):
        _pin_limiter(client)
        assert client.get("/geocode", params={"q": "shattuck"}).status_code == 200
        resp = client.get("/geocode", params={"q": "telegraph"})
        assert resp.status_code == 429
        # RFC 9110 delta-seconds: an integer, and never 0 (which would invite an
        # immediate retry that is guaranteed to fail again).
        retry_after = resp.headers["Retry-After"]
        assert retry_after.isdigit() and int(retry_after) >= 1
        # Refused, not queued: the upstream saw exactly one request.
        assert len(FakeAsyncClient.calls) == 1

    def test_the_budget_recovers(self, client):
        clock = _pin_limiter(client)
        client.get("/geocode", params={"q": "shattuck"})
        assert client.get("/geocode", params={"q": "telegraph"}).status_code == 429
        clock.advance(1.1)
        assert client.get("/geocode", params={"q": "telegraph"}).status_code == 200

    def test_a_cache_hit_costs_no_token(self, client):
        """The cache is consulted before the bucket, because a repeat query
        places no load on Nominatim at all. Per-process, so each replica warms
        its own — a lower hit rate, not a correctness problem."""
        _pin_limiter(client)
        assert client.get("/geocode", params={"q": "shattuck"}).status_code == 200
        for _ in range(5):
            again = client.get("/geocode", params={"q": "Shattuck "})
            assert again.status_code == 200
        assert len(FakeAsyncClient.calls) == 1

    def test_an_unavailable_limiter_refuses_rather_than_serving(self, client):
        """Fail closed (ADR-0013). 503 rather than 429: it is our dependency
        that is broken, not the caller's behaviour."""
        class DeadLimiter:
            async def acquire(self):
                raise LimiterUnavailable("connection refused")

            async def aclose(self):
                pass

        client.app.state.app_state.limiter = DeadLimiter()
        resp = client.get("/geocode", params={"q": "shattuck"})
        assert resp.status_code == 503
        assert "Retry-After" in resp.headers
        assert FakeAsyncClient.calls == []

    def test_the_identifying_user_agent_survives(self, client):
        """Nominatim's policy requires a UA identifying the application and a
        way to reach its operator. Sharing the bucket must not lose it."""
        _pin_limiter(client)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        client.get("/geocode", params={"q": "shattuck"})
        assert (FakeAsyncClient.calls[0]["headers"]["User-Agent"]
                == cfg["api"]["nominatim_user_agent"])

    def test_the_contact_override_survives(self, client, monkeypatch):
        _pin_limiter(client)
        monkeypatch.setenv("SR_NOMINATIM_CONTACT", "sr/0.1 (+mailto:ops@example.com)")
        client.get("/geocode", params={"q": "shattuck"})
        assert (FakeAsyncClient.calls[0]["headers"]["User-Agent"]
                == "sr/0.1 (+mailto:ops@example.com)")
