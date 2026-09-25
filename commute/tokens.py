"""Tester tokens (ADR-0017, ADR-0018): how a beta device identifies itself
before accounts exist (Phase 5).

The owner mints one per device, offline, and hands it over out of band:

    python -m commute.tokens issue --label derek-iphone
    python -m commute.tokens list
    python -m commute.tokens revoke 3

`issue` prints the token once. Only its SHA-256 is stored, so a copy of the
database cannot be used to upload as a tester. A token is 256 bits from
`secrets`, so an unsalted fast hash is the right tool here: there is nothing
to brute-force, unlike a password. Clients send `Authorization: Bearer <token>`.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from commute.store import Clock, TraceStore, resolve_db_path, wall_clock_ms

# Recognizable in a paste or a leak ("sr" route planner, "t" tester).
TOKEN_PREFIX = "srt_"


@dataclass(frozen=True)
class IssuedToken:
    id: int
    label: str
    token: str            # the plaintext: shown once, never stored


@dataclass(frozen=True)
class Tester:
    """An authenticated request's tester token."""
    id: int
    label: str


@dataclass(frozen=True)
class TesterToken:
    """A row of `list`: never the token, never its hash."""
    id: int
    label: str
    created_at: int
    revoked_at: int | None
    trips: int


def mint() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(store: TraceStore, label: str) -> IssuedToken:
    label = label.strip()
    if not label:
        raise ValueError("a tester token needs a non-empty label")
    token = mint()
    with store.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO tester_tokens (label, token_sha256, created_at) VALUES (?, ?, ?)",
            (label, hash_token(token), store.clock()))
        token_id = cur.lastrowid
    assert token_id is not None
    return IssuedToken(id=token_id, label=label, token=token)


def authenticate(store: TraceStore, token: str) -> Tester | None:
    """The active tester token matching `token`, or None if it is unknown or
    revoked. Looked up by hash, so no comparison ever touches the plaintext."""
    with store.connect() as conn:
        row = conn.execute(
            "SELECT id, label FROM tester_tokens"
            " WHERE token_sha256 = ? AND revoked_at IS NULL",
            (hash_token(token),)).fetchone()
    return Tester(id=row[0], label=row[1]) if row else None


def list_tokens(store: TraceStore) -> list[TesterToken]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT k.id, k.label, k.created_at, k.revoked_at, COUNT(t.trip_id)"
            " FROM tester_tokens k LEFT JOIN trips t ON t.token_id = k.id"
            " GROUP BY k.id ORDER BY k.id").fetchall()
    return [TesterToken(id=r[0], label=r[1], created_at=r[2], revoked_at=r[3], trips=r[4])
            for r in rows]


def revoke(store: TraceStore, token_id: int) -> bool:
    """False if there is no such token. Revoking twice keeps the first time.
    The token's trips stay: revocation stops uploads, it does not erase."""
    with store.transaction() as conn:
        row = conn.execute("SELECT revoked_at FROM tester_tokens WHERE id = ?",
                           (token_id,)).fetchone()
        if row is None:
            return False
        if row[0] is None:
            conn.execute("UPDATE tester_tokens SET revoked_at = ? WHERE id = ?",
                         (store.clock(), token_id))
        return True


def _utc(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.UTC).strftime(
        "%Y-%m-%d %H:%M")


def main(argv: Sequence[str] | None = None, clock: Clock | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m commute.tokens",
        description="Mint, list and revoke tester tokens (ADR-0018).")
    parser.add_argument("--db", help="SQLite file (default: $SR_COMMUTE_DB, else "
                                     "data/commute/commute.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_issue = sub.add_parser("issue", help="mint a token for one device; printed once")
    p_issue.add_argument("--label", required=True,
                         help="who or what the device is, e.g. derek-iphone")
    sub.add_parser("list", help="every token, with its status and trip count")
    p_revoke = sub.add_parser("revoke", help="stop a token from uploading")
    p_revoke.add_argument("token_id", type=int, help="the id shown by `list`")
    args = parser.parse_args(argv)

    if args.command == "issue" and not args.label.strip():
        print("error: --label must not be empty", file=sys.stderr)
        return 2

    store = TraceStore(resolve_db_path(args.db), clock or wall_clock_ms)
    store.init_schema()

    if args.command == "issue":
        issued = issue(store, args.label)
        print(f'Issued tester token #{issued.id} for "{issued.label}". '
              "It is shown only this once:")
        print(issued.token)
        return 0

    if args.command == "revoke":
        if not revoke(store, args.token_id):
            print(f"error: no tester token with id {args.token_id}", file=sys.stderr)
            return 1
        print(f"Revoked tester token #{args.token_id}.")
        return 0

    rows = list_tokens(store)
    if not rows:
        print("No tester tokens.")
        return 0
    print(f"{'id':>4}  {'status':<8} {'created (UTC)':<17} {'trips':>5}  label")
    for t in rows:
        status = "active" if t.revoked_at is None else "revoked"
        print(f"{t.id:>4}  {status:<8} {_utc(t.created_at):<17} {t.trips:>5}  {t.label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
