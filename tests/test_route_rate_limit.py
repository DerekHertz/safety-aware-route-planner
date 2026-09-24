"""`POST /route` and `POST /reroute` carry a PER-CLIENT quota.

The geocode limiter (ADR-0013) is one global bucket because it rations *our*
calls to a third party. Routing is the opposite shape of problem — the cost is
ours, and the thing being protected is our own CPU — so a global bucket would
be a denial of service against the whole product: one busy client would refuse
everybody. Three properties follow, and they are what this file pins:

  * **Clients get separate buckets**, so one caller's abuse cannot throttle
    another's trip.
  * **A client cannot mint identities.** `X-Forwarded-For` is caller-supplied
    and therefore worthless unless you know how many proxies you actually run.
    With none configured — the default, and the local/CI condition — the header
    is not read at all, and a spoofed one buys nothing.
  * **Redis being down does not take routing down.** This limiter fails OPEN,
    which is deliberately the opposite of the geocode limiter's answer; see the
    amendment to ADR-0013 for why the two differ.

Redis is not required to run this file, and neither is the `redis` package: the
shared path is driven by the same injected-fake-client pattern
`tests/test_geocode_rate_limit.py` established.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.ratelimit import (
    InProcessTokenBucket,
    LimiterUnavailable,
    PerClientLimiter,
    RedisTokenBucket,
    bucket_step,
    build_route_limiter,
    client_key,
    route_rate_limit_key,
    trusted_proxies,
)
from pyref.config import DEFAULT_CONFIG_PATH, Config
from tests.helpers.fixtures import unprotected_left_city


def run(coro):
    """Drive one coroutine to completion — same reason as the geocode suite:
    a handful of coroutines that never await anything slow do not justify an
    async-test plugin."""
    return asyncio.run(coro)


class FakeClock:
    """Hand-cranked monotonic clock, so a refill is arithmetic rather than a
    30-second sleep."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRedisClient:
    """The one call `RedisTokenBucket` makes, executed against `bucket_step` —
    the same pure function the Lua transcribes. A shared `store` models two
    replicas contending on one key."""

    def __init__(self, store: dict, clock: FakeClock, fail: bool = False):
        self.store = store
        self.clock = clock
        self.fail = fail
        self.keys: list[str] = []

    async def eval(self, script, numkeys, key, rate, capacity):
        self.keys.append(key)
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


# --- who is the client? ----------------------------------------------------


class TestClientKey:
    """`client_key` is pure for the same reason `bucket_step` is: the trust
    boundary is the part that has to be obviously right, and reasoning about it
    should not require constructing an ASGI request."""

    def test_with_no_trusted_proxies_the_header_is_not_read_at_all(self):
        """The default. `X-Forwarded-For` is caller-supplied; reading it without
        knowing the proxy depth lets anyone mint unlimited identities, which is
        strictly worse than no limit because it still looks like it works."""
        assert client_key("203.0.113.9", "1.1.1.1, 2.2.2.2", hops=0) == "203.0.113.9"

    def test_a_spoofed_header_cannot_mint_identities_by_default(self):
        keys = {
            client_key("203.0.113.9", f"10.0.0.{i}", hops=0)
            for i in range(50)
        }
        assert keys == {"203.0.113.9"}

    def test_one_proxy_means_the_last_entry_is_the_caller(self):
        """Behind one load balancer every peer address is the balancer, so the
        header is the only way to tell callers apart. The balancer appends the
        address it saw, so the RIGHTMOST entry is the one it vouches for."""
        assert client_key("10.0.0.7", "198.51.100.4", hops=1) == "198.51.100.4"

    def test_one_proxy_ignores_everything_the_caller_prepended(self):
        """The attack: the caller sends its own `X-Forwarded-For`, the balancer
        appends the real peer, and a left-to-right reader believes the caller.
        Counting from the right is what makes the forged prefix inert."""
        assert client_key("10.0.0.7", "evil-1, evil-2, 198.51.100.4", hops=1) \
            == "198.51.100.4"
        minted = {
            client_key("10.0.0.7", f"evil-{i}, 198.51.100.4", hops=1)
            for i in range(50)
        }
        assert minted == {"198.51.100.4"}

    def test_two_proxies_skip_the_inner_hop(self):
        """CDN -> load balancer -> app: the CDN appended the caller, the
        balancer appended the CDN. Two hops back from the right is the caller."""
        assert client_key("10.0.0.7", "198.51.100.4, 192.0.2.1", hops=2) \
            == "198.51.100.4"

    def test_too_few_entries_falls_back_to_the_peer(self):
        """A request that did not come through the configured proxies (a direct
        hit on the container, or a misconfigured count) has no trustworthy
        entry to read, so it is keyed by the socket — never by whatever the
        caller happened to send."""
        assert client_key("203.0.113.9", "", hops=1) == "203.0.113.9"
        assert client_key("203.0.113.9", None, hops=1) == "203.0.113.9"
        assert client_key("203.0.113.9", "1.1.1.1", hops=2) == "203.0.113.9"

    def test_a_missing_peer_still_yields_a_key(self):
        """`request.client` is None for some transports. One shared bucket for
        those is acceptable; a crash in the limiter is not."""
        assert client_key(None, None, hops=0) == "unknown"
        assert client_key(None, "  ", hops=1) == "unknown"

    def test_whitespace_around_entries_is_not_a_second_identity(self):
        """`X-Forwarded-For: a, b` is conventionally comma-SPACE separated, so
        failing to strip would make ` 1.2.3.4` and `1.2.3.4` different clients
        — a free identity per space character."""
        assert client_key("10.0.0.7", " 198.51.100.4 ", hops=1) == "198.51.100.4"


class TestTrustedProxyConfig:
    def test_the_default_is_to_trust_nothing(self, monkeypatch):
        """The safe default is the one that cannot be attacked, even though it
        degrades to a per-deployment bucket behind an unconfigured balancer."""
        monkeypatch.delenv("SR_TRUSTED_PROXIES", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert trusted_proxies(cfg) == 0

    def test_the_environment_supplies_a_deployment_fact(self, monkeypatch):
        monkeypatch.setenv("SR_TRUSTED_PROXIES", " 2 ")
        assert trusted_proxies(Config.load(DEFAULT_CONFIG_PATH)) == 2

    def test_nonsense_reads_as_zero_rather_than_as_trust(self, monkeypatch):
        """A typo must fail toward the safe behaviour. Reading `SR_TRUSTED_PROXIES=one`
        as 'trust one hop' would hand out identities on a shell quoting mistake."""
        monkeypatch.setenv("SR_TRUSTED_PROXIES", "one")
        assert trusted_proxies(Config.load(DEFAULT_CONFIG_PATH)) == 0
        monkeypatch.setenv("SR_TRUSTED_PROXIES", "-3")
        assert trusted_proxies(Config.load(DEFAULT_CONFIG_PATH)) == 0


# --- the per-client bucket -------------------------------------------------


class TestPerClientLimiter:
    def test_one_client_burning_its_burst_does_not_refuse_another(self):
        """The whole reason this is not the geocode limiter. A single global
        bucket on /route would let any one caller throttle every other caller,
        which is a denial of service with no packets to spare."""
        clock = FakeClock()
        limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(rate=0.5, capacity=2.0, clock=clock))
        assert run(limiter.acquire("a")) is None
        assert run(limiter.acquire("a")) is None
        assert run(limiter.acquire("a")) is not None   # 'a' is over budget
        assert run(limiter.acquire("b")) is None       # 'b' is untouched

    def test_a_refused_client_is_told_how_long_to_wait_and_recovers(self):
        clock = FakeClock()
        limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(rate=0.5, capacity=1.0, clock=clock))
        assert run(limiter.acquire("a")) is None
        assert run(limiter.acquire("a")) == pytest.approx(2.0)  # 1 token / 0.5 per s
        clock.advance(2.0)
        assert run(limiter.acquire("a")) is None

    def test_the_tracked_set_is_bounded(self):
        """Per-client state keyed by an attacker-chosen string is a memory
        exhaustion primitive if it is unbounded. The cap is why this is an LRU
        and not a dict."""
        clock = FakeClock()
        limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(rate=0.5, capacity=1.0, clock=clock),
            max_clients=8)
        for i in range(100):
            run(limiter.acquire(f"c{i}"))
        assert limiter.tracked == 8

    def test_eviction_is_least_recently_used_so_a_hot_client_keeps_its_bucket(self):
        """Eviction hands back a full bucket, so evicting the *active* client
        would be a limit-bypass: cycle N identities, get your own reset for
        free. LRU means the caller you are rate-limiting is the last one
        evicted, and an attacker must spend `max_clients` requests to buy back
        one burst."""
        clock = FakeClock()
        limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(rate=0.5, capacity=1.0, clock=clock),
            max_clients=2)
        assert run(limiter.acquire("hot")) is None
        assert run(limiter.acquire("hot")) is not None     # spent
        for i in range(3):
            run(limiter.acquire(f"cold{i}"))               # forces an eviction
            assert run(limiter.acquire("hot")) is not None  # keeps 'hot' recent
        assert run(limiter.acquire("hot")) is not None     # still empty, not reset
        assert limiter.tracked == 2

    def test_the_shared_backend_gives_each_client_its_own_key(self):
        """One Redis key per client, under a common prefix — the per-deployment
        ceiling and the per-client ceiling are different objects."""
        clock, store = FakeClock(), {}
        redis = FakeRedisClient(store, clock)
        limiter = PerClientLimiter(
            lambda key: RedisTokenBucket(redis, f"sr:route:{key}", 0.5, 1.0))
        assert run(limiter.acquire("1.2.3.4")) is None
        assert run(limiter.acquire("5.6.7.8")) is None
        assert redis.keys == ["sr:route:1.2.3.4", "sr:route:5.6.7.8"]

    def test_a_client_is_capped_across_replicas_not_per_replica(self):
        """Same property the geocode bucket has, applied per client: two API
        processes must not give one caller twice the quota."""
        clock, store = FakeClock(), {}

        def replica():
            redis = FakeRedisClient(store, clock)
            return PerClientLimiter(
                lambda key: RedisTokenBucket(redis, f"sr:route:{key}", 0.5, 1.0))

        replica_a, replica_b = replica(), replica()
        assert run(replica_a.acquire("1.2.3.4")) is None
        assert run(replica_b.acquire("1.2.3.4")) == pytest.approx(2.0)
        assert run(replica_b.acquire("9.9.9.9")) is None

    def test_an_unreachable_redis_surfaces_rather_than_being_swallowed_here(self):
        """The limiter still reports the truth; the fail-open decision belongs
        to the endpoint, which is where the cost of each answer is known."""
        clock = FakeClock()
        redis = FakeRedisClient({}, clock, fail=True)
        limiter = PerClientLimiter(
            lambda key: RedisTokenBucket(redis, f"sr:route:{key}", 0.5, 1.0))
        with pytest.raises(LimiterUnavailable):
            run(limiter.acquire("1.2.3.4"))


class TestRouteLimiterConstruction:
    def test_no_redis_url_means_in_process_buckets(self, monkeypatch):
        monkeypatch.delenv("SR_REDIS_URL", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        limiter = build_route_limiter(cfg)
        assert isinstance(limiter, PerClientLimiter)
        run(limiter.acquire("1.2.3.4"))
        assert isinstance(limiter.bucket_for("1.2.3.4"), InProcessTokenBucket)

    def test_an_injected_client_selects_the_shared_backend(self, monkeypatch):
        monkeypatch.delenv("SR_REDIS_URL", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        limiter = build_route_limiter(cfg, client=FakeRedisClient({}, FakeClock()))
        run(limiter.acquire("1.2.3.4"))
        bucket = limiter.bucket_for("1.2.3.4")
        assert isinstance(bucket, RedisTokenBucket)
        assert bucket.key.startswith(route_rate_limit_key(cfg))

    def test_the_routing_key_is_not_the_geocode_key(self, monkeypatch):
        """Sharing a key would mean the search box and the router drawing from
        one budget — two unrelated ceilings welded together."""
        monkeypatch.delenv("SR_ROUTE_RATE_LIMIT_KEY", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert route_rate_limit_key(cfg) != cfg["api"]["rate_limit_key"]

    def test_the_routing_key_is_overridable_for_a_shared_redis(self, monkeypatch):
        monkeypatch.setenv("SR_ROUTE_RATE_LIMIT_KEY", "sr:staging:route")
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        assert route_rate_limit_key(cfg) == "sr:staging:route"

    def test_the_bucket_is_built_from_the_configured_rate_and_burst(self, monkeypatch):
        monkeypatch.delenv("SR_REDIS_URL", raising=False)
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        limiter = build_route_limiter(cfg)
        run(limiter.acquire("1.2.3.4"))
        bucket = limiter.bucket_for("1.2.3.4")
        assert bucket.rate == pytest.approx(float(cfg["api"]["route_rate_per_s"]))
        assert bucket.capacity == float(cfg["api"]["route_burst"])


class TestConfiguredQuotaIsGenerousForRealUse:
    """The numbers are a product decision, so they are asserted against the
    front-end's actual call pattern rather than left as bare constants."""

    def test_a_planning_session_never_comes_close(self):
        """`web/app/page.tsx` debounces /route at REROUTE_DEBOUNCE_MS = 1500, so
        the most a person can emit is one request per 1.5 s, and only while
        continuously changing inputs. Ten plans back-to-back — far more than
        moving two pins and dragging the safety slider — must all be served."""
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        clock = FakeClock()
        limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(
                rate=float(cfg["api"]["route_rate_per_s"]),
                capacity=float(cfg["api"]["route_burst"]), clock=clock))
        for _ in range(10):
            assert run(limiter.acquire("planner")) is None
            clock.advance(1.5)

    def test_an_hour_of_live_navigation_never_comes_close(self):
        """A successful reroute swaps the followed route's identity, which
        resets `useRouteProgress`'s START_GRACE_MS (15 s) and its 3-fix
        off-route hysteresis — so the client cannot ask for more than roughly
        one reroute per 18 s no matter how lost the traveler is."""
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        clock = FakeClock()
        limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(
                rate=float(cfg["api"]["route_rate_per_s"]),
                capacity=float(cfg["api"]["route_burst"]), clock=clock))
        for _ in range(200):          # ~1 hour at one reroute per 18 s
            assert run(limiter.acquire("driver")) is None
            clock.advance(18.0)

    def test_a_flat_out_attacker_is_refused_quickly(self):
        """The reason the ceiling exists: /route is 3-8 graph searches and is
        unauthenticated. An unthrottled caller is a CPU exhaustion primitive."""
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        clock = FakeClock()          # no advance: requests as fast as they arrive
        limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(
                rate=float(cfg["api"]["route_rate_per_s"]),
                capacity=float(cfg["api"]["route_burst"]), clock=clock))
        admitted = sum(1 for _ in range(500) if run(limiter.acquire("bot")) is None)
        assert admitted == int(cfg["api"]["route_burst"])


# --- the endpoints ---------------------------------------------------------


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    """Builds the real app over a toy pack. `proxies` is set BEFORE the app is
    created because the trusted-proxy depth is a deployment fact, resolved once
    at startup like the CORS origins."""
    pack, ids = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    monkeypatch.delenv("SR_REDIS_URL", raising=False)
    clients = []

    def build(proxies: int = 0, burst: float = 3.0):
        monkeypatch.setenv("SR_TRUSTED_PROXIES", str(proxies))
        from api.main import create_app
        app = create_app()
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        clock = FakeClock()
        app.state.app_state.route_limiter = PerClientLimiter(
            lambda key: InProcessTokenBucket(rate=0.5, capacity=burst, clock=clock))
        c.ids, c.pack, c.clock = ids, pack, clock
        return c

    yield build
    for c in clients:
        c.__exit__(None, None, None)


def _route_body(pack, ids):
    o, d = ids["s"], ids["a0"]
    return {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "departure_time": "2026-07-24T08:30:00",
        "safety_enabled": True,
    }


def _reroute_body(pack, ids):
    body = _route_body(pack, ids)
    body.pop("departure_time")
    body.pop("safety_enabled")
    # Deliberately left as a v1-shaped preference (no traffic_basis): the
    # quota suite exercises every /reroute path, so leaving it v1 keeps a
    # second, incidental guard on the backward compatibility that
    # test_route_artifact_v2.py asserts head-on.
    body["preference"] = {"level": "fast", "lambda": 0.0,
                          "detour_budget_pct": 0.25,
                          "departure_time": "2026-07-24T08:30:00"}
    return body


class TestRouteEndpoint:
    def test_under_the_quota_route_is_unchanged(self, make_client):
        """The wire contract is frozen (ADR-0004). A quota adds a status code,
        never a field — anything else would have to be hand-mirrored into
        web/lib/types.ts."""
        c = make_client()
        resp = c.post("/route", json=_route_body(c.pack, c.ids))
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"routes"}
        assert 1 <= len(data["routes"]) <= 3
        assert data["routes"][0]["schema_version"] == 2

    def test_over_the_quota_is_429_with_a_retry_after(self, make_client):
        c = make_client(burst=2.0)
        for _ in range(2):
            assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 200
        resp = c.post("/route", json=_route_body(c.pack, c.ids))
        assert resp.status_code == 429
        retry_after = resp.headers["Retry-After"]
        assert retry_after.isdigit() and int(retry_after) >= 1
        # The error body is FastAPI's standard {"detail": ...}, the same shape
        # /route already returns for a 422. No new field, no schema sync.
        assert set(resp.json().keys()) == {"detail"}

    def test_the_quota_recovers(self, make_client):
        c = make_client(burst=1.0)
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 200
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 429
        c.clock.advance(2.0)
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 200

    def test_reroute_draws_on_the_same_bucket(self, make_client):
        """One quota for both, because both spend the same resource — a graph
        search in the threadpool. Two ceilings would mean the real ceiling is
        their sum, which nobody would have written down anywhere."""
        c = make_client(burst=2.0)
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 200
        assert c.post("/reroute", json=_reroute_body(c.pack, c.ids)).status_code == 200
        assert c.post("/reroute", json=_reroute_body(c.pack, c.ids)).status_code == 429

    def test_a_refusal_costs_no_search(self, make_client):
        """The point of refusing at the edge. If the 429 were raised after the
        search, the limiter would be decoration."""
        c = make_client(burst=1.0)
        c.post("/route", json=_route_body(c.pack, c.ids))
        calls = []
        real_route = c.app.state.app_state.registry.only().router.route

        def counting(*a, **kw):
            calls.append(1)
            return real_route(*a, **kw)

        c.app.state.app_state.registry.only().router.route = counting
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 429
        assert calls == []
        # The spy is on the Router the handler actually selects: once the
        # bucket refills, an admitted request is counted. Without this, an
        # empty `calls` could mean the spy was simply never reached.
        c.clock.advance(2.0)
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 200
        assert calls == [1]

    def test_geocode_and_meta_are_not_charged_to_the_routing_quota(self, make_client):
        """Separate ceilings for separate resources: /meta is a dict lookup and
        /geocode has its own, differently-motivated budget."""
        c = make_client(burst=1.0)
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 200
        assert c.get("/meta").status_code == 200
        assert c.get("/health").status_code == 200
        assert c.post("/route", json=_route_body(c.pack, c.ids)).status_code == 429


class TestClientIdentityThroughTheEndpoint:
    def test_a_spoofed_forwarded_header_is_ignored_by_default(self, make_client):
        """The attack this defends against: vary a header, get a fresh bucket
        every request. With no trusted proxies configured the header is never
        read, so all of these are the same client."""
        c = make_client(proxies=0, burst=1.0)
        assert c.post("/route", json=_route_body(c.pack, c.ids),
                      headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        for i in range(5):
            resp = c.post("/route", json=_route_body(c.pack, c.ids),
                          headers={"X-Forwarded-For": f"9.9.9.{i}"})
            assert resp.status_code == 429

    def test_with_one_trusted_proxy_two_callers_do_not_share_a_bucket(self, make_client):
        """Behind a balancer the peer address is the balancer, so without this
        the per-client bucket silently degenerates into one global bucket — the
        denial of service this design exists to avoid."""
        c = make_client(proxies=1, burst=1.0)
        alice = {"X-Forwarded-For": "198.51.100.4"}
        bob = {"X-Forwarded-For": "203.0.113.7"}
        assert c.post("/route", json=_route_body(c.pack, c.ids),
                      headers=alice).status_code == 200
        assert c.post("/route", json=_route_body(c.pack, c.ids),
                      headers=alice).status_code == 429
        # Bob's trip is unaffected by Alice's burst.
        assert c.post("/route", json=_route_body(c.pack, c.ids),
                      headers=bob).status_code == 200

    def test_with_one_trusted_proxy_a_prepended_header_still_cannot_mint(self, make_client):
        """The caller controls the left of the header; the balancer owns the
        right. Counting hops from the right is the whole defence."""
        c = make_client(proxies=1, burst=1.0)
        assert c.post("/route", json=_route_body(c.pack, c.ids),
                      headers={"X-Forwarded-For": "evil-0, 198.51.100.4"}
                      ).status_code == 200
        for i in range(1, 6):
            resp = c.post("/route", json=_route_body(c.pack, c.ids),
                          headers={"X-Forwarded-For": f"evil-{i}, 198.51.100.4"})
            assert resp.status_code == 429


class TestRedisOutage:
    def test_routing_fails_OPEN_when_the_limiter_cannot_answer(self, make_client):
        """**Opposite of the geocode limiter, on purpose.** ADR-0013 fails
        closed because that ceiling protects a third party's policy and the
        penalty for breaching it is a ban. This ceiling protects our own CPU:
        failing closed would turn a Redis blip into a total outage of the
        product's core endpoint, and would do it to every replica at once.
        Unthrottled routing for the length of an outage is a load problem;
        503 on every route request is the product being down."""
        class DeadLimiter:
            async def acquire(self, key):
                raise LimiterUnavailable("connection refused")

            async def aclose(self):
                pass

        c = make_client()
        c.app.state.app_state.route_limiter = DeadLimiter()
        for _ in range(3):
            resp = c.post("/route", json=_route_body(c.pack, c.ids))
            assert resp.status_code == 200
            assert set(resp.json().keys()) == {"routes"}
        assert c.post("/reroute",
                      json=_reroute_body(c.pack, c.ids)).status_code == 200
