"""Tester tokens (ADR-0017, ADR-0018): minted offline by the owner, shown once,
stored only as a hash, revocable. There are no accounts until Phase 5.

    python -m commute.tokens issue --label <name>
    python -m commute.tokens list
    python -m commute.tokens revoke <id>
"""
from __future__ import annotations

import hashlib

import pytest

from commute import tokens
from commute.store import TraceStore
from tests.helpers.commute import FakeClock, bearer, chunk, chunk_url, commute_client, new_trip_id


def _cli(db, *argv, clock=None) -> int:
    return tokens.main(["--db", str(db), *argv], clock=clock or FakeClock())


def _issued_token(out: str) -> str:
    [line] = [ln for ln in out.splitlines() if ln.startswith(tokens.TOKEN_PREFIX)]
    return line.strip()


def test_issue_prints_the_token_once_and_stores_only_its_hash(tmp_path, capsys):
    db = tmp_path / "c.sqlite3"
    assert _cli(db, "issue", "--label", "derek-iphone") == 0
    out = capsys.readouterr().out
    token = _issued_token(out)
    assert out.count(token) == 1
    assert len(token) >= len(tokens.TOKEN_PREFIX) + 40

    raw = db.read_bytes()
    assert token.encode() not in raw
    assert hashlib.sha256(token.encode()).hexdigest().encode() in raw


def test_every_issued_token_is_different(tmp_path, capsys):
    db = tmp_path / "c.sqlite3"
    _cli(db, "issue", "--label", "a")
    first = _issued_token(capsys.readouterr().out)
    _cli(db, "issue", "--label", "a")
    second = _issued_token(capsys.readouterr().out)
    assert first != second


def test_an_issued_token_authenticates_against_the_service(tmp_path, capsys):
    db = tmp_path / "c.sqlite3"
    _cli(db, "issue", "--label", "derek-iphone")
    token = _issued_token(capsys.readouterr().out)
    with commute_client(db, FakeClock()) as client:
        resp = client.get("/v1/me", headers=bearer(token))
    assert resp.status_code == 200
    assert resp.json() == {"label": "derek-iphone"}


def test_list_shows_labels_and_status_but_never_a_token(tmp_path, capsys):
    db = tmp_path / "c.sqlite3"
    _cli(db, "issue", "--label", "derek-iphone")
    token = _issued_token(capsys.readouterr().out)
    assert _cli(db, "list") == 0
    out = capsys.readouterr().out
    assert "derek-iphone" in out
    assert "active" in out
    assert token not in out
    assert hashlib.sha256(token.encode()).hexdigest() not in out


def test_revoke_locks_the_token_out_and_list_says_so(tmp_path, capsys):
    db = tmp_path / "c.sqlite3"
    clock = FakeClock()
    _cli(db, "issue", "--label", "derek-iphone", clock=clock)
    token = _issued_token(capsys.readouterr().out)
    [info] = tokens.list_tokens(TraceStore(db, clock))

    with commute_client(db, clock) as client:
        trip = new_trip_id()
        assert client.put(chunk_url(trip, 0), json=chunk(2),
                          headers=bearer(token)).status_code == 201
        assert _cli(db, "revoke", str(info.id), clock=clock) == 0
        assert client.put(chunk_url(trip, 1), json=chunk(2),
                          headers=bearer(token)).status_code == 401

    capsys.readouterr()
    _cli(db, "list", clock=clock)
    out = capsys.readouterr().out
    assert "revoked" in out
    [info] = tokens.list_tokens(TraceStore(db, clock))
    assert info.revoked_at == clock()
    assert info.trips == 1


def test_revoking_an_unknown_id_fails(tmp_path, capsys):
    db = tmp_path / "c.sqlite3"
    assert _cli(db, "revoke", "42") == 1
    assert "42" in capsys.readouterr().err


def test_revoking_twice_is_harmless_and_keeps_the_first_time(tmp_path):
    clock = FakeClock()
    store = TraceStore(tmp_path / "c.sqlite3", clock)
    store.init_schema()
    issued = tokens.issue(store, "a")
    assert tokens.revoke(store, issued.id) is True
    first = clock()
    clock.advance_ms(5_000)
    assert tokens.revoke(store, issued.id) is True
    [info] = tokens.list_tokens(store)
    assert info.revoked_at == first


@pytest.mark.parametrize("label", ["", "   "])
def test_a_label_is_required(tmp_path, label, capsys):
    db = tmp_path / "c.sqlite3"
    assert _cli(db, "issue", "--label", label) != 0
    store = TraceStore(db, FakeClock())
    store.init_schema()
    assert tokens.list_tokens(store) == []
