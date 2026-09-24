"""The commute planner's datastore: one SQLite file, created on startup,
versioned, and never somewhere git would pick it up (ADR-0018)."""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from commute.store import (
    DEFAULT_DB_PATH,
    SCHEMA_VERSION,
    SchemaTooNew,
    TraceStore,
    resolve_db_path,
)
from tests.helpers.commute import FakeClock, commute_client

REPO_ROOT = Path(__file__).resolve().parent.parent


def _version(db: Path) -> list[int]:
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute("SELECT version FROM schema_version")]


def test_init_is_idempotent_and_records_the_version(tmp_path):
    db = tmp_path / "c.sqlite3"
    store = TraceStore(db, FakeClock())
    store.init_schema()
    store.init_schema()
    assert _version(db) == [SCHEMA_VERSION]


def test_startup_creates_the_schema_and_its_directory(tmp_path):
    db = tmp_path / "nested" / "dir" / "c.sqlite3"
    with commute_client(db, FakeClock()) as client:
        assert client.get("/health").status_code == 200
    assert _version(db) == [SCHEMA_VERSION]


def test_a_database_from_a_newer_schema_refuses_to_start(tmp_path):
    """Rolling the code back must not scribble on a newer layout."""
    db = tmp_path / "c.sqlite3"
    TraceStore(db, FakeClock()).init_schema()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    with pytest.raises(SchemaTooNew):
        TraceStore(db, FakeClock()).init_schema()


def test_trace_data_is_append_only_in_the_database_itself(tmp_path):
    """Not just by convention in the handlers: a stored chunk body cannot be
    rewritten, a purged one cannot be restored, and an ETA log row cannot be
    edited, whatever code path tries."""
    from commute import tokens
    from commute.schemas import TraceChunk, TripEnd
    from tests.helpers.commute import chunk, new_trip_id, trip_end

    store = TraceStore(tmp_path / "c.sqlite3", FakeClock())
    store.init_schema()
    token_id = tokens.issue(store, "a").id
    trip = new_trip_id()
    store.put_chunk(token_id, trip, 0, TraceChunk.model_validate(chunk(2)))
    store.end_trip(token_id, trip, TripEnd.model_validate(trip_end()))

    with store.connect() as conn:
        for sql in ("UPDATE trace_chunks SET body = '{}'",
                    "UPDATE trace_chunks SET fix_count = 99",
                    "UPDATE trip_ends SET arrived = 0"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
        conn.execute("UPDATE trace_chunks SET body = NULL, purged_at = 1")   # the purge
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE trace_chunks SET body = '{}'")


def test_the_path_comes_from_the_argument_then_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SR_COMMUTE_DB", str(tmp_path / "env.sqlite3"))
    assert resolve_db_path(tmp_path / "arg.sqlite3") == tmp_path / "arg.sqlite3"
    assert resolve_db_path() == tmp_path / "env.sqlite3"
    monkeypatch.delenv("SR_COMMUTE_DB")
    assert resolve_db_path() == DEFAULT_DB_PATH


def test_the_default_database_is_gitignored():
    """The store holds location history of real people; it must never be one
    `git add -A` away from the repository."""
    rel = DEFAULT_DB_PATH.relative_to(REPO_ROOT).as_posix()
    result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO_ROOT,
                            capture_output=True)
    if result.returncode in (0, 1):
        assert result.returncode == 0, f"{rel} is not gitignored"
    else:
        # git cannot read this checkout (a Windows-made worktree seen from
        # WSL records a path it cannot resolve), so check the rule directly.
        ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert rel.split("/")[0] + "/" in ignored, f"{rel} is not gitignored"


def test_building_the_app_touches_no_disk(tmp_path):
    """`scripts/dump_openapi.py` builds the app to read its schema, with no
    database anywhere; only startup may create one."""
    from commute.app import create_app
    db = tmp_path / "never" / "c.sqlite3"
    create_app(db_path=db, clock=FakeClock()).openapi()
    assert not db.parent.exists()
