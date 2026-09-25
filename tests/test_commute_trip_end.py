"""`POST /v1/trips/{trip_id}/end`: the predicted-versus-actual ETA log.

ADR-0011 wants predicted-versus-actual arrival logged from day one, and
ADR-0017 starts that log with the beta. Ending a trip is where it is written:
when the trip ended, whether it arrived, and what the artifact being followed
at that moment predicted. Times only; the endpoints never leave the phone.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from commute import tokens
from commute.store import TraceStore
from tests.helpers.commute import (
    T0_MS,
    FakeClock,
    bearer,
    chunk,
    chunk_url,
    commute_client,
    end_url,
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


def end(env, trip_id, body, token=None):
    return env.client.post(end_url(trip_id), json=body,
                           headers=bearer(token or env.alice))


def test_ending_a_trip_writes_one_eta_log_row(env):
    trip = new_trip_id()
    assert env.client.put(chunk_url(trip, 0), json=chunk(3),
                          headers=bearer(env.alice)).status_code == 201
    env.clock.advance_ms(700_000)
    resp = end(env, trip, trip_end(ended_at=T0_MS + 600_000, eta_s=540.0))
    assert resp.status_code == 201
    assert resp.json() == {"trip_id": trip, "created": True}

    [row] = env.store.eta_log()
    assert row.trip_id == trip
    assert row.tester_label == "alice-phone"
    assert row.ended_at == T0_MS + 600_000
    assert row.arrived is True
    assert row.predicted_effective_at == T0_MS
    assert row.predicted_eta_s == 540.0
    assert row.level == "safe"
    assert row.profile_version == "abc123"
    assert row.received_at == T0_MS + 700_000
    # the number the log exists for: actual minus predicted, seconds
    assert row.eta_error_s == pytest.approx(60.0)


def test_an_identical_resend_is_a_200_no_op(env):
    trip = new_trip_id()
    assert end(env, trip, trip_end()).status_code == 201
    resp = end(env, trip, trip_end())
    assert resp.status_code == 200
    assert resp.json() == {"trip_id": trip, "created": False}
    assert len(env.store.eta_log()) == 1


def test_a_different_end_for_an_ended_trip_is_409(env):
    trip = new_trip_id()
    assert end(env, trip, trip_end(arrived=True)).status_code == 201
    resp = end(env, trip, trip_end(arrived=False))
    assert resp.status_code == 409
    assert isinstance(resp.json()["detail"], str)
    assert env.store.eta_log()[0].arrived is True


def test_end_may_arrive_before_any_chunk_and_still_claims_the_trip(env):
    """Retries come back in any order, and a trip shorter than the two 300 m
    trims uploads no fixes at all. Either way the end is logged."""
    trip = new_trip_id()
    assert end(env, trip, trip_end()).status_code == 201
    assert env.client.put(chunk_url(trip, 0), json=chunk(3),
                          headers=bearer(env.alice)).status_code == 201
    assert env.client.put(chunk_url(trip, 1), json=chunk(3),
                          headers=bearer(env.bob)).status_code == 404


def test_another_token_cannot_end_someone_elses_trip(env):
    trip = new_trip_id()
    assert env.client.put(chunk_url(trip, 0), json=chunk(3),
                          headers=bearer(env.alice)).status_code == 201
    assert end(env, trip, trip_end(), token=env.bob).status_code == 404
    assert env.store.eta_log() == []


def test_a_trip_with_no_artifact_logs_a_null_prediction(env):
    trip = new_trip_id()
    body = {"ended_at": T0_MS, "arrived": False, "prediction": None}
    assert end(env, trip, body).status_code == 201
    [row] = env.store.eta_log()
    assert row.predicted_eta_s is None
    assert row.eta_error_s is None


def test_an_abandoned_trip_has_no_eta_error(env):
    """Stopping navigation early is not an arrival, so there is no actual
    arrival time to compare against."""
    trip = new_trip_id()
    assert end(env, trip, trip_end(arrived=False)).status_code == 201
    assert env.store.eta_log()[0].eta_error_s is None


def test_a_v1_artifact_has_no_profile_version(env):
    body = trip_end()
    body["prediction"]["profile_version"] = None
    assert end(env, new_trip_id(), body).status_code == 201


def _without(key: str) -> dict:
    body = trip_end()
    del body[key]
    return body


def _prediction(**overrides) -> dict:
    body = trip_end()
    body["prediction"].update(overrides)
    return body


BAD_ENDS = {
    "missing ended_at": _without("ended_at"),
    "missing arrived": _without("arrived"),
    "missing prediction": _without("prediction"),
    "arrived as a string": {**trip_end(), "arrived": "yes"},
    "ended_at in seconds": {**trip_end(), "ended_at": T0_MS // 1000},
    "negative eta": _prediction(eta_s=-1.0),
    "prediction after the end": _prediction(effective_at=T0_MS + 600_001),
    "empty level": _prediction(level=""),
}


@pytest.mark.parametrize("name", sorted(BAD_ENDS))
def test_invalid_ends_are_422_with_a_string_detail(env, name):
    trip = new_trip_id()
    resp = end(env, trip, BAD_ENDS[name])
    assert resp.status_code == 422, resp.text
    assert isinstance(resp.json()["detail"], str)
    assert env.store.eta_log() == []


def test_ending_needs_a_token(env):
    assert env.client.post(end_url(new_trip_id()), json=trip_end()).status_code == 401
