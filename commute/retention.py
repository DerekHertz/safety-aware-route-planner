"""Raw trip traces are kept 90 days (ADR-0017).

    python -m commute.retention purge

Run it daily; scheduling it is part of deploying the service (a follow-up).

What is purged is each chunk's body: its fixes and the route artifacts it
carried, which is every coordinate this service holds. What stays carries no
location and is kept: the trip, the chunk's hash, receipt time, fix count and
first/last fix time, and the whole predicted-versus-actual ETA log. Keeping the
hash is what lets a late retry of a purged chunk still be answered as a replay
instead of being stored again.

The clock is the SERVER's receipt time, not the fixes' own timestamps: a phone
clock can be wrong, and the period should run from when the data was collected.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from collections.abc import Sequence

from commute.store import Clock, TraceStore, resolve_db_path, wall_clock_ms

RAW_RETENTION_DAYS = 90
_DAY_MS = 86_400_000


def purge_raw_traces(store: TraceStore, max_age_days: int = RAW_RETENTION_DAYS) -> int:
    """Clear the body of every chunk received more than `max_age_days` ago.
    Returns how many were cleared; idempotent.

    The store's connections run with `secure_delete`, so the cleared text is
    overwritten on disk rather than left in free pages.
    """
    now = store.clock()
    cutoff = now - max_age_days * _DAY_MS
    with store.transaction() as conn:
        cur = conn.execute(
            "UPDATE trace_chunks SET body = NULL, purged_at = ?"
            " WHERE body IS NOT NULL AND received_at < ?", (now, cutoff))
        return cur.rowcount


def main(argv: Sequence[str] | None = None, clock: Clock | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m commute.retention",
        description=f"Purge raw trip traces older than {RAW_RETENTION_DAYS} days (ADR-0017).")
    parser.add_argument("--db", help="SQLite file (default: $SR_COMMUTE_DB, else "
                                     "data/commute/commute.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("purge", help="clear raw fixes and artifacts past the retention period")
    args = parser.parse_args(argv)

    store = TraceStore(resolve_db_path(args.db), clock or wall_clock_ms)
    store.init_schema()
    purged = purge_raw_traces(store)
    cutoff = store.clock() - RAW_RETENTION_DAYS * _DAY_MS
    before = datetime.datetime.fromtimestamp(cutoff / 1000, tz=datetime.UTC)
    print(f"Purged {purged} chunk(s) received before {before:%Y-%m-%d %H:%M} UTC.",
          file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
