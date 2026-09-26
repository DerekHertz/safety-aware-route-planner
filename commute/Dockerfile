# syntax=docker/dockerfile:1
#
# The commute planner service (ADR-0018). Build from the repo root:
#
#     docker build -f commute/Dockerfile -t sr-commute .
#
# commute/Dockerfile.dockerignore (BuildKit reads it in place of the root
# .dockerignore) lets in only commute/ and its requirements, so this image
# cannot contain the engine even by accident: the boundary ADR-0018 draws in
# tests/test_commute_boundary.py holds for the artifact too.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SR_COMMUTE_DB=/data/commute.sqlite3

WORKDIR /app

COPY requirements-commute.txt .
RUN pip install --no-cache-dir -r requirements-commute.txt

COPY commute/ commute/

# The SQLite file lives on a volume mounted at /data. Create and own the
# directory here, so a fresh named volume inherits the non-root owner.
RUN mkdir -p /data \
 && useradd --create-home --uid 10001 app \
 && chown app:app /data
USER app
VOLUME ["/data"]

EXPOSE 8100

# One process, no --workers: every write goes to one SQLite file, and the
# service is I/O-light (a phone uploads a chunk about every 2 minutes).
CMD ["uvicorn", "commute.app:app", "--host", "0.0.0.0", "--port", "8100"]
