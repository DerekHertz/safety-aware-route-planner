"""The commute planner's datastore: one SQLite file (ADR-0018).

Stdlib `sqlite3`, a connection per call, and every write in a `BEGIN
IMMEDIATE` transaction, so the check-then-insert that makes an upload
idempotent is atomic even with several worker processes on one file.

**Trace data is append-only.** A chunk or a trip end is inserted once and
never rewritten; the only change ever made to a stored chunk is the retention
purge clearing its body (`commute/retention.py`). Triggers enforce this, so a
future code path cannot quietly edit history. What a purge leaves behind
(hash, receipt time, fix count, first and last fix time) carries no location.

The file holds location history of real people. It is never exported, and it
lives under `data/`, which is gitignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from commute.schemas import TraceChunk, TripEnd

Clock = Callable[[], int]
"""Returns now, in milliseconds since the Unix epoch. Injected for tests."""

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "commute" / "commute.sqlite3"
SCHEMA_VERSION = 1


def wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def resolve_db_path(path: str | os.PathLike[str] | None = None) -> Path:
    """The argument, else `SR_COMMUTE_DB`, else `DEFAULT_DB_PATH`."""
    if path is not None:
        return Path(path)
    env = os.environ.get("SR_COMMUTE_DB")
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


class SchemaTooNew(RuntimeError):
    """The file was written by a newer version of this service."""


class Outcome(Enum):
    CREATED = "created"      # stored now
    REPLAYED = "replayed"    # identical content already held; nothing stored
    CONFLICT = "conflict"    # different content already held under this key
    NOT_YOURS = "not_yours"  # the trip belongs to another tester token


@dataclass(frozen=True)
class StoredChunk:
    trip_id: str
    seq: int
    received_at: int
    fix_count: int
    first_t: int | None
    last_t: int | None
    body: dict[str, Any] | None      # None once purged
    purged_at: int | None


@dataclass(frozen=True)
class EtaLogRow:
    """One row of the predicted-versus-actual ETA log (ADR-0011)."""
    trip_id: str
    tester_label: str
    received_at: int
    ended_at: int
    arrived: bool
    predicted_effective_at: int | None
    predicted_eta_s: float | None
    level: str | None
    profile_version: str | None

    @property
    def eta_error_s(self) -> float | None:
        """Actual minus predicted arrival, in seconds; positive means late.
        Only an arrived trip with a prediction has one."""
        if (not self.arrived or self.predicted_eta_s is None
                or self.predicted_effective_at is None):
            return None
        predicted_arrival_ms = self.predicted_effective_at + self.predicted_eta_s * 1000.0
        return (self.ended_at - predicted_arrival_ms) / 1000.0


def canonical_json(value: Any) -> str:
    """The form that is hashed and stored: keys sorted, no whitespace. Two
    uploads are "the same chunk" when this string is the same."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def content_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_VERSION_TABLE = "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"

# Instants are INTEGER epoch milliseconds throughout: the client's (t,
# effective_at, ended_at) and the server's own (created_at, received_at, ...).
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tester_tokens (
        id           INTEGER PRIMARY KEY,
        label        TEXT    NOT NULL,
        token_sha256 TEXT    NOT NULL UNIQUE,
        created_at   INTEGER NOT NULL,
        revoked_at   INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS trips (
        trip_id    TEXT    PRIMARY KEY,
        token_id   INTEGER NOT NULL REFERENCES tester_tokens(id),
        created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS trace_chunks (
        trip_id        TEXT    NOT NULL REFERENCES trips(trip_id),
        seq            INTEGER NOT NULL,
        received_at    INTEGER NOT NULL,
        content_sha256 TEXT    NOT NULL,
        fix_count      INTEGER NOT NULL,
        first_t        INTEGER,
        last_t         INTEGER,
        body           TEXT,
        purged_at      INTEGER,
        PRIMARY KEY (trip_id, seq)
    )""",
    """CREATE INDEX IF NOT EXISTS trace_chunks_unpurged
        ON trace_chunks (received_at) WHERE body IS NOT NULL""",
    # The ETA log. Times and the prediction only: no location, so it is kept
    # when the raw trace is purged.
    """CREATE TABLE IF NOT EXISTS trip_ends (
        trip_id                TEXT    PRIMARY KEY REFERENCES trips(trip_id),
        received_at            INTEGER NOT NULL,
        content_sha256         TEXT    NOT NULL,
        ended_at               INTEGER NOT NULL,
        arrived                INTEGER NOT NULL,
        predicted_effective_at INTEGER,
        predicted_eta_s        REAL,
        level                  TEXT,
        profile_version        TEXT
    )""",
    # Append-only, enforced. The one permitted update clears a body (and
    # stamps purged_at); a body can never be written back or edited.
    """CREATE TRIGGER IF NOT EXISTS trace_chunks_append_only
        BEFORE UPDATE ON trace_chunks
        WHEN NEW.body IS NOT NULL
          OR NEW.trip_id IS NOT OLD.trip_id
          OR NEW.seq IS NOT OLD.seq
          OR NEW.received_at IS NOT OLD.received_at
          OR NEW.content_sha256 IS NOT OLD.content_sha256
          OR NEW.fix_count IS NOT OLD.fix_count
          OR NEW.first_t IS NOT OLD.first_t
          OR NEW.last_t IS NOT OLD.last_t
        BEGIN
          SELECT RAISE(ABORT, 'trace chunks are append-only; only a purge may clear a body');
        END""",
    """CREATE TRIGGER IF NOT EXISTS trip_ends_append_only
        BEFORE UPDATE ON trip_ends
        BEGIN
          SELECT RAISE(ABORT, 'trip ends are append-only');
        END""",
)


class TraceStore:
    """Constructing one touches no disk; `init_schema()` (run at startup, and
    by each CLI) creates the file, its directory and the tables."""

    def __init__(self, path: str | os.PathLike[str], clock: Clock = wall_clock_ms):
        self.path = Path(path)
        self.clock = clock

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # isolation_level=None: no implicit transactions; `transaction()`
        # opens them explicitly. secure_delete zeroes freed content, so a
        # purged trace does not linger in free pages.
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA secure_delete = ON")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction that takes the write lock up front, so a
        read-then-insert inside it cannot race another writer."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def init_schema(self) -> None:
        """Idempotent. Refuses a file from a newer schema rather than writing
        into a layout it does not understand."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            conn.execute(_VERSION_TABLE)
            versions = [r[0] for r in conn.execute("SELECT version FROM schema_version")]
            if not versions:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)",
                             (SCHEMA_VERSION,))
            elif versions != [SCHEMA_VERSION]:
                if max(versions) > SCHEMA_VERSION:
                    raise SchemaTooNew(
                        f"{self.path} has schema version {max(versions)}; this "
                        f"code knows only up to {SCHEMA_VERSION}")
                raise RuntimeError(f"{self.path} has an unreadable schema_version table")
            for statement in _SCHEMA:
                conn.execute(statement)

    def ping(self) -> None:
        with self.connect() as conn:
            conn.execute("SELECT version FROM schema_version").fetchone()

    # --- writes ----------------------------------------------------------

    def _claim_trip(self, conn: sqlite3.Connection, trip_id: str, token_id: int) -> bool:
        """True if the trip is this token's, creating it on first sight. A
        trip belongs to whichever token first wrote to it."""
        row = conn.execute("SELECT token_id FROM trips WHERE trip_id = ?",
                           (trip_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO trips (trip_id, token_id, created_at) VALUES (?, ?, ?)",
                         (trip_id, token_id, self.clock()))
            return True
        return bool(row[0] == token_id)

    def put_chunk(self, token_id: int, trip_id: str, seq: int, chunk: TraceChunk) -> Outcome:
        body = canonical_json(chunk.model_dump(mode="json"))
        digest = content_digest(body)
        times = [f.t for f in chunk.fixes]
        with self.transaction() as conn:
            if not self._claim_trip(conn, trip_id, token_id):
                return Outcome.NOT_YOURS
            row = conn.execute(
                "SELECT content_sha256 FROM trace_chunks WHERE trip_id = ? AND seq = ?",
                (trip_id, seq)).fetchone()
            if row is not None:
                return Outcome.REPLAYED if row[0] == digest else Outcome.CONFLICT
            conn.execute(
                "INSERT INTO trace_chunks (trip_id, seq, received_at, content_sha256,"
                " fix_count, first_t, last_t, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (trip_id, seq, self.clock(), digest, len(times),
                 times[0] if times else None, times[-1] if times else None, body))
            return Outcome.CREATED

    def end_trip(self, token_id: int, trip_id: str, end: TripEnd) -> Outcome:
        digest = content_digest(canonical_json(end.model_dump(mode="json")))
        p = end.prediction
        with self.transaction() as conn:
            if not self._claim_trip(conn, trip_id, token_id):
                return Outcome.NOT_YOURS
            row = conn.execute("SELECT content_sha256 FROM trip_ends WHERE trip_id = ?",
                               (trip_id,)).fetchone()
            if row is not None:
                return Outcome.REPLAYED if row[0] == digest else Outcome.CONFLICT
            conn.execute(
                "INSERT INTO trip_ends (trip_id, received_at, content_sha256, ended_at,"
                " arrived, predicted_effective_at, predicted_eta_s, level, profile_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trip_id, self.clock(), digest, end.ended_at, int(end.arrived),
                 p.effective_at if p else None, p.eta_s if p else None,
                 p.level if p else None, p.profile_version if p else None))
            return Outcome.CREATED

    # --- reads -----------------------------------------------------------

    def read_chunk(self, trip_id: str, seq: int) -> StoredChunk | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT trip_id, seq, received_at, fix_count, first_t, last_t, body, purged_at"
                " FROM trace_chunks WHERE trip_id = ? AND seq = ?", (trip_id, seq)).fetchone()
        if row is None:
            return None
        return StoredChunk(trip_id=row[0], seq=row[1], received_at=row[2], fix_count=row[3],
                           first_t=row[4], last_t=row[5],
                           body=json.loads(row[6]) if row[6] is not None else None,
                           purged_at=row[7])

    def eta_log(self) -> list[EtaLogRow]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT e.trip_id, k.label, e.received_at, e.ended_at, e.arrived,"
                " e.predicted_effective_at, e.predicted_eta_s, e.level, e.profile_version"
                " FROM trip_ends e JOIN trips t ON t.trip_id = e.trip_id"
                " JOIN tester_tokens k ON k.id = t.token_id"
                " ORDER BY e.ended_at, e.trip_id").fetchall()
        return [EtaLogRow(trip_id=r[0], tester_label=r[1], received_at=r[2], ended_at=r[3],
                          arrived=bool(r[4]), predicted_effective_at=r[5],
                          predicted_eta_s=r[6], level=r[7], profile_version=r[8])
                for r in rows]
