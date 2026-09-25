"""Raw trip traces are kept 90 days (ADR-0017); the ETA log is kept.

    python -m commute.retention purge

The clock is injected, so "90 days later" is arithmetic. Retention runs from
when the SERVER received a chunk, not from the phone's own timestamps: a phone
clock can be wrong, the server's is the one this service controls.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from commute import retention, tokens
from commute.store import TraceStore
from tests.helpers.commute import (
    DAY_MS,
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
    token = tokens.issue(store, "alice-phone").token
    with commute_client(db, clock) as client:
        yield SimpleNamespace(client=client, store=store, clock=clock, token=token, db=db)


def put(env, trip, seq, body):
    return env.client.put(chunk_url(trip, seq), json=body, headers=bearer(env.token))


def test_the_retention_period_is_ninety_days():
    assert retention.RAW_RETENTION_DAYS == 90


def test_chunks_are_kept_through_day_ninety_and_purged_after(env):
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201

    env.clock.advance_days(90)
    assert retention.purge_raw_traces(env.store) == 0
    assert env.store.read_chunk(trip, 0).body is not None

    env.clock.advance_ms(1)
    assert retention.purge_raw_traces(env.store) == 1
    stored = env.store.read_chunk(trip, 0)
    assert stored.body is None
    assert stored.purged_at == T0_MS + 90 * DAY_MS + 1
    # what remains carries no location: counts and times only
    assert stored.fix_count == 3


def test_only_old_chunks_are_purged(env):
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    env.clock.advance_days(60)
    assert put(env, trip, 1, chunk(3, t0=T0_MS + 60 * DAY_MS)).status_code == 201
    env.clock.advance_days(31)
    assert retention.purge_raw_traces(env.store) == 1
    assert env.store.read_chunk(trip, 0).body is None
    assert env.store.read_chunk(trip, 1).body is not None


def test_purge_is_idempotent(env):
    assert put(env, new_trip_id(), 0, chunk(3)).status_code == 201
    env.clock.advance_days(91)
    assert retention.purge_raw_traces(env.store) == 1
    assert retention.purge_raw_traces(env.store) == 0


def test_the_eta_log_survives_the_purge(env):
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    assert env.client.post(end_url(trip), json=trip_end(),
                           headers=bearer(env.token)).status_code == 201
    env.clock.advance_days(365)
    assert retention.purge_raw_traces(env.store) == 1
    [row] = env.store.eta_log()
    assert row.trip_id == trip
    assert row.eta_error_s == pytest.approx(60.0)


def test_a_late_retry_of_a_purged_chunk_is_not_stored_again(env):
    """A phone that was offline for three months may still hold the chunk.
    It gets the same 200 as any replay, and the purge stands."""
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(3)).status_code == 201
    env.clock.advance_days(91)
    retention.purge_raw_traces(env.store)

    resp = put(env, trip, 0, chunk(3))
    assert resp.status_code == 200
    assert resp.json()["created"] is False
    assert env.store.read_chunk(trip, 0).body is None
    assert put(env, trip, 0, chunk(4)).status_code == 409


def test_purged_coordinates_are_not_left_in_the_database_file(env):
    """SQLite normally leaves deleted content in free pages until they are
    reused. The purge zeroes it, so the location history is actually gone."""
    trip = new_trip_id()
    assert put(env, trip, 0, chunk(50)).status_code == 201
    assert b"37.8712345" in env.db.read_bytes()
    env.clock.advance_days(91)
    assert retention.purge_raw_traces(env.store) == 1
    assert b"37.8712345" not in env.db.read_bytes()
    assert b"122.2687654" not in env.db.read_bytes()


def test_the_cli_purges_and_reports_a_count(env, capsys):
    assert put(env, new_trip_id(), 0, chunk(3)).status_code == 201
    env.clock.advance_days(91)
    assert retention.main(["--db", str(env.db), "purge"], clock=env.clock) == 0
    assert "Purged 1 chunk" in capsys.readouterr().out
    assert retention.purge_raw_traces(env.store) == 0
