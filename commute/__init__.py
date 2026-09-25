"""The commute planner service (ADR-0011), started early as a trip-trace ingest.

Its first slice (ADR-0017, contract in ADR-0018) receives **trip traces** from
beta testers, identified by a **tester token** instead of an account, and keeps
the predicted-versus-actual ETA log. It is a separate, stateful service: it
imports nothing from the engine or the route service, and reaches routing only
through route artifacts the client hands it (`tests/test_commute_boundary.py`).

    uvicorn commute.app:app --port 8100          # the service
    python -m commute.tokens issue --label NAME  # mint a tester token
    python -m commute.retention purge            # drop raw traces past 90 days

`SR_COMMUTE_DB` names the SQLite file (default `data/commute/commute.sqlite3`,
gitignored). It holds location history of real people: never export it.
"""
