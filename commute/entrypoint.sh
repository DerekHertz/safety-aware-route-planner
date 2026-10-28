#!/bin/sh
# Container entrypoint for the commute service (the `commute` Dockerfile stage).
#
# Runs as root just long enough to hand the database directory to the app
# user: a Fly volume mounts root-owned, and the service should not write
# location history as root.
#
# Then two processes under one shell:
#   - a daily `python -m commute.retention purge` (ADR-0017's 90-day limit).
#     It runs at boot and every 24 h after. It lives in the container rather
#     than in a Fly scheduled machine because a volume attaches to exactly one
#     machine, and this one holds it. A restart only runs it early, and the
#     purge is idempotent.
#   - uvicorn, exec'd so it receives the platform's stop signal directly.
set -eu

db="${SR_COMMUTE_DB:-data/commute/commute.sqlite3}"
db_dir=$(dirname "$db")
mkdir -p "$db_dir"
chown -R app:app "$db_dir"

as_app() { setpriv --reuid=app --regid=app --init-groups "$@"; }

(
  while :; do
    as_app python -m commute.retention purge || echo "[commute] retention purge failed"
    sleep 86400
  done
) &

exec setpriv --reuid=app --regid=app --init-groups \
  uvicorn commute.app:app --host 0.0.0.0 --port "${PORT:-8080}"
