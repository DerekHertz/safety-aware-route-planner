"""`PUT /v1/trips/{trip_id}/chunks/{seq}`: the trip-trace ingest (ADR-0018).

The client uploads a trip trace in chunks and retries anything unsent on its
next launch (ADR-0017), so it will send the same chunk more than once and in no
particular order. What this file pins:

  * a resend of the SAME chunk is a harmless 200, and a DIFFERENT chunk under
    the same `(trip, seq)` is a 409 that keeps the first one;
  * a trip belongs to the tester token that created it;
  * validation is hard, and a rejection never echoes a coordinate back.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from commute import tokens
from commute.schemas import MAX_BODY_BYTES, MAX_FIXES_PER_CHUNK
from commute.store import TraceStore
from tests.helpers.commute import (
    LAT0,
    LON0,
    T0_MS,
    FakeClock,
    artifact,
    bearer,
    chunk,
    chunk_url,
    commute_client,
    end_url,
    fix,
    fixes,
    new_trip_id,
    trip_end,
)


@pytest.fixture()
def env(tmp_path):
    clock = FakeClock()
    db = tmp_path / "commute.sqlite3"
    store = TraceStore(db, clock)
    store.init_schema()
    alice = tokens.issue(store, "alice-phone").token
    bob = tokens.issue(store, "bob-phone").token
    with commute_client(db, clock) as client:
        yield SimpleNamespace(client=client, store=store, clock=clock,
                              alice=alice, bob=bob)


def put(env, trip_id, seq, body, token=None):
    return env.client.put(chunk_url(trip_id, seq), json=body,
                          headers=bearer(token or env.alice))


# --- storing and replaying ---------------------------------------------------


def test_first_put_stores_the_chunk_and_answers_201(env):
    trip = new_trip_id()
    resp = put(env, trip, 0, chunk(5))
    assert resp.status_code == 201
    assert resp.json() == {"trip_id": trip, "seq": 0, "created": True}
    stored = env.store.read_chunk(trip, 0)
    assert stored is not None
    assert stored.fix_count == 5
    assert stored.body == {"fixes": fixes(5), "artifacts": []}
    assert stored.received_at == T0_MS


def test_an_identical_resend_is_a_200_no_op(env):
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(5)).status_code == 201
    env.clock.advance_ms(60_000)
    resp = put(env, trip, 0, chunk(5))
    assert resp.status_code == 200
    assert resp.json() == {"trip_id": trip, "seq": 0, "created": False}
    # the original receipt time stands: nothing was re-stored
    assert env.store.read_chunk(trip, 0).received_at == T0_MS


def test_identity_is_the_parsed_content_not_the_bytes(env):
    """A client that re-serializes a stored chunk may reorder keys or change
    whitespace; that is still the same chunk."""
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    reordered = {"fixes": [dict(reversed(list(f.items()))) for f in fixes(3)]}
    raw = json.dumps(reordered, indent=4)
    resp = env.client.put(chunk_url(trip, 0), content=raw,
                          headers={**bearer(env.alice),
                                   "Content-Type": "application/json"})
    assert resp.status_code == 200
    assert resp.json()["created"] is False


def test_different_content_under_the_same_seq_is_409_and_the_first_is_kept(env):
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(5)).status_code == 201
    resp = put(env, trip, 0, chunk(6))
    assert resp.status_code == 409
    assert isinstance(resp.json()["detail"], str)
    assert env.store.read_chunk(trip, 0).fix_count == 5


def test_chunks_may_arrive_in_any_order(env):
    trip = new_trip_id()
    for seq in (2, 0, 1):
        body = chunk(3, t0=T0_MS + seq * 120_000)
        assert put(env, trip, seq, body).status_code == 201
    assert [env.store.read_chunk(trip, s).fix_count for s in (0, 1, 2)] == [3, 3, 3]


def test_the_same_seq_on_another_trip_is_a_different_chunk(env):
    a, b = new_trip_id(), new_trip_id()
    assert put(env, a, 0, chunk(3)).status_code == 201
    assert put(env, b, 0, chunk(4)).status_code == 201


def test_trip_id_is_canonicalized(env):
    """A UUID spelled in upper case is the same trip."""
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    resp = put(env, trip.upper(), 0, chunk(3))
    assert resp.status_code == 200
    assert resp.json()["trip_id"] == trip


def test_artifacts_are_stored_opaquely_with_their_chunk(env):
    trip = new_trip_id()
    resp = put(env, trip, 0, chunk(3, with_artifact=True))
    assert resp.status_code == 201
    body = env.store.read_chunk(trip, 0).body
    assert body["artifacts"] == [{"effective_at": T0_MS, "artifact": artifact()}]


def test_a_chunk_may_carry_only_a_reroute_artifact(env):
    trip = new_trip_id()
    body = {"fixes": [], "artifacts": [{"effective_at": T0_MS,
                                        "artifact": artifact(300.0)}]}
    assert put(env, trip, 3, body).status_code == 201


def test_unknown_fields_are_ignored_not_stored(env):
    """Forward compatibility: a newer client may send more than this server
    knows. It is dropped, and does not change the chunk's identity."""
    trip = new_trip_id()
    body = chunk(2)
    body["fixes"][0]["altitude_m"] = 12.0
    body["battery"] = 0.4
    assert put(env, trip, 0, body).status_code == 201
    assert env.store.read_chunk(trip, 0).body == {"fixes": fixes(2), "artifacts": []}
    assert put(env, trip, 0, chunk(2)).status_code == 200


def test_a_late_chunk_after_the_trip_ended_is_still_accepted(env):
    """A killed PWA retries on its next launch, in whatever order its queue
    comes back. Refusing a late chunk would only lose trace data."""
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    assert env.client.post(end_url(trip), json=trip_end(),
                           headers=bearer(env.alice)).status_code == 201
    assert put(env, trip, 1, chunk(3, t0=T0_MS + 120_000)).status_code == 201


# --- ownership ---------------------------------------------------------------


def test_a_trip_belongs_to_the_token_that_created_it(env):
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    # a different chunk, and even an identical replay, are refused as unknown
    assert put(env, trip, 1, chunk(3), token=env.bob).status_code == 404
    resp = put(env, trip, 0, chunk(3), token=env.bob)
    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)
    assert env.store.read_chunk(trip, 1) is None


# --- authentication ----------------------------------------------------------


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Basic YWxpY2U6cGFzc3dvcmQ="},
    {"Authorization": "Bearer"},
    {"Authorization": "Bearer srt_not-a-real-token"},
])
def test_missing_or_unknown_tokens_are_401(env, headers):
    resp = env.client.put(chunk_url(new_trip_id(), 0), json=chunk(3), headers=headers)
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert isinstance(resp.json()["detail"], str)


def test_a_revoked_token_is_401_and_its_trips_are_kept(env):
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    alice_id = next(t.id for t in tokens.list_tokens(env.store) if t.label == "alice-phone")
    assert tokens.revoke(env.store, alice_id) is True
    assert put(env, trip, 1, chunk(3)).status_code == 401
    assert env.store.read_chunk(trip, 0) is not None


def test_me_names_the_token_so_a_client_can_check_it_at_opt_in(env):
    resp = env.client.get("/v1/me", headers=bearer(env.alice))
    assert resp.status_code == 200
    assert resp.json() == {"label": "alice-phone"}
    assert env.client.get("/v1/me").status_code == 401


# --- validation ----------------------------------------------------------------


def _with_fix(**overrides) -> dict:
    body = chunk(3)
    body["fixes"][1] = {**body["fixes"][1], **overrides}
    return body


BAD_CHUNKS = {
    "lat above 90": _with_fix(lat=90.1234567),
    "lat below -90": _with_fix(lat=-90.1234567),
    "lon above 180": _with_fix(lon=180.1234567),
    "lon below -180": _with_fix(lon=-180.1234567),
    "negative accuracy": _with_fix(accuracy_m=-1.0),
    "missing accuracy": {"fixes": [{k: v for k, v in f.items() if k != "accuracy_m"}
                                   for f in fixes(2)]},
    "negative speed": _with_fix(speed_mps=-0.5),
    "heading above 360": _with_fix(heading_deg=360.5),
    "fractional t": _with_fix(t=T0_MS + 1000.5),
    "t as a string": _with_fix(t=str(T0_MS + 1000)),
    "t in seconds, not ms": {"fixes": [fix(T0_MS // 1000)]},
    "repeated t": {"fixes": [fix(T0_MS), fix(T0_MS)]},
    "t going backwards": {"fixes": [fix(T0_MS + 1000), fix(T0_MS)]},
    "too many fixes": {"fixes": fixes(MAX_FIXES_PER_CHUNK + 1)},
    "nothing at all": {"fixes": []},
    "no fixes key": {"artifacts": [{"effective_at": T0_MS, "artifact": artifact()}]},
    "artifact not an object": {"fixes": fixes(1),
                               "artifacts": [{"effective_at": T0_MS, "artifact": [1, 2]}]},
    "artifacts out of order": {"fixes": fixes(1), "artifacts": [
        {"effective_at": T0_MS + 5, "artifact": artifact()},
        {"effective_at": T0_MS, "artifact": artifact()}]},
}


@pytest.mark.parametrize("name", sorted(BAD_CHUNKS))
def test_invalid_chunks_are_422_with_a_string_detail(env, name):
    trip = new_trip_id()
    resp = put(env, trip, 0, BAD_CHUNKS[name])
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and detail
    # the rejection names the field, never the value
    for leaked in ("1234567", str(LAT0), str(LON0)):
        assert leaked not in resp.text
    assert env.store.read_chunk(trip, 0) is None


def test_the_detail_names_the_field_by_its_path(env):
    resp = put(env, new_trip_id(), 0, _with_fix(lat=90.1234567))
    assert resp.json()["detail"].startswith("fixes[1].lat: ")


def test_non_finite_numbers_are_rejected(env):
    """Python's JSON parser accepts NaN and Infinity literals; the store must not."""
    raw = json.dumps({"fixes": [fix(T0_MS)]}).replace("5.0", "NaN", 1)
    assert "NaN" in raw
    resp = env.client.put(chunk_url(new_trip_id(), 0), content=raw,
                          headers={**bearer(env.alice), "Content-Type": "application/json"})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_non_finite_numbers_inside_an_artifact_are_rejected(env):
    """The artifact is opaque, but it is still stored as strict JSON."""
    raw = json.dumps(chunk(1, with_artifact=True)).replace('"eta_s": 600.0', '"eta_s": Infinity')
    assert "Infinity" in raw
    resp = env.client.put(chunk_url(new_trip_id(), 0), content=raw,
                          headers={**bearer(env.alice), "Content-Type": "application/json"})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_malformed_json_is_422_with_a_string_detail(env):
    resp = env.client.put(chunk_url(new_trip_id(), 0), content=b"{not json",
                          headers={**bearer(env.alice), "Content-Type": "application/json"})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


@pytest.mark.parametrize("path", [
    "/v1/trips/not-a-uuid/chunks/0",
    f"/v1/trips/{new_trip_id()}/chunks/-1",
    f"/v1/trips/{new_trip_id()}/chunks/1000000",
    f"/v1/trips/{new_trip_id()}/chunks/one",
])
def test_bad_path_parameters_are_422(env, path):
    resp = env.client.put(path, json=chunk(3), headers=bearer(env.alice))
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


def test_an_oversized_body_is_413_before_it_is_parsed(env):
    padding = "x" * (MAX_BODY_BYTES + 1)
    body = chunk(1)
    body["artifacts"] = [{"effective_at": T0_MS, "artifact": {"pad": padding}}]
    resp = put(env, new_trip_id(), 0, body)
    assert resp.status_code == 413
    assert isinstance(resp.json()["detail"], str)


def test_an_oversized_body_without_content_length_is_413(env):
    """Chunked transfer encoding carries no Content-Length to check up front."""
    def stream():
        yield b'{"fixes": [], "artifacts": [{"effective_at": 1790000000000, "artifact": {"pad": "'
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield b"x" * 65536
        yield b'"}}]}'

    resp = env.client.put(chunk_url(new_trip_id(), 0), content=stream(),
                          headers={**bearer(env.alice), "Content-Type": "application/json"})
    assert resp.status_code == 413


def test_an_oversized_body_is_drained_before_the_413_is_sent():
    """A 413 sent while the peer is still writing makes the server close a
    half-read connection, and a proxy in front (the web container's /commute
    rewrite) reports ECONNRESET as a 500, which the client retries forever.
    Seen in the compose-stack CI job. So every body message is consumed first.
    """
    from commute.app import BodySizeLimit

    parts = [b"x" * 65536] * 20
    messages = [{"type": "http.request", "body": p, "more_body": i < len(parts) - 1}
                for i, p in enumerate(parts)]
    events: list[str] = []

    async def receive():
        events.append("receive")
        return messages.pop(0)

    async def send(message):
        events.append(message["type"])

    async def app(scope, receive, send):  # never reached
        raise AssertionError("an oversized body reached the app")

    scope = {"type": "http", "headers": [(b"content-length", str(20 * 65536).encode())]}
    asyncio.run(BodySizeLimit(app, max_bytes=65536)(scope, receive, send))

    assert messages == []
    assert events[:20] == ["receive"] * 20            # every body message read ...
    assert events[20] == "http.response.start"        # ... before the 413 starts


# --- privacy -----------------------------------------------------------------


def test_nothing_logged_during_ingest_contains_a_coordinate(env, caplog):
    """The store holds location history of real people (ADR-0018): request
    handling logs no bodies, on success or on rejection."""
    caplog.set_level(logging.DEBUG)
    trip = new_trip_id()
    put(env, trip, 0, chunk(3, with_artifact=True))
    put(env, trip, 0, chunk(4))                      # 409
    put(env, trip, 1, _with_fix(lat=91.1234567))     # 422
    put(env, trip, 2, chunk(3), token=env.bob)       # 404
    for leaked in ("37.871", "122.268", "1234567"):
        assert leaked not in caplog.text


# --- health and CORS ------------------------------------------------------------


def test_health_needs_no_token(env):
    resp = env.client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_cors_preflight_allows_the_configured_origin_to_send_a_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_COMMUTE_CORS_ORIGINS", "https://beta.example.test")
    with commute_client(tmp_path / "c.sqlite3", FakeClock()) as client:
        resp = client.options(chunk_url(new_trip_id(), 0), headers={
            "Origin": "https://beta.example.test",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://beta.example.test"
    allowed = resp.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
