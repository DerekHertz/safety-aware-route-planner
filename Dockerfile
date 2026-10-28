# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — compile the sr_core C++ extension.
#
# Kept separate so the runtime image ships no compiler and no build headers.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY core/ core/

# `pip wheel`, not `pip install -e`: an editable install would require core/
# and setuptools to still be present at runtime, whereas a wheel is a clean
# artefact that either built or didn't.
RUN pip install --no-cache-dir "pybind11>=2.12" "setuptools>=68" wheel \
 && pip wheel --no-cache-dir --no-deps -w /wheels ./core

# ---------------------------------------------------------------------------
# Commute service image (`docker build --target commute`, fly.commute.toml).
#
# A separate image from the route API: commute/ imports nothing from the
# engine, so it ships no numpy, scipy, sr_core or packs. It sits before the
# runtime stage because the LAST stage is the default target, and a plain
# `docker build .` must keep producing the route API.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS commute

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The same pins as the route API, taken from requirements.txt rather than
# restated, so the two services never drift onto different FastAPI versions.
COPY requirements.txt .
RUN grep -E '^(fastapi|pydantic|uvicorn)' requirements.txt > /tmp/req.txt \
 && pip install --no-cache-dir -r /tmp/req.txt \
 && rm /tmp/req.txt

COPY commute/ commute/

# Starts as root only to take ownership of the mounted volume (Fly mounts it
# root-owned), then drops to the app user; see commute/entrypoint.sh.
RUN useradd --create-home --uid 10001 app \
 && chmod +x commute/entrypoint.sh

EXPOSE 8080
CMD ["commute/entrypoint.sh"]

# ---------------------------------------------------------------------------
# Stage 2 — runtime.
# ---------------------------------------------------------------------------
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a source edit does not invalidate this layer.
# requirements.txt is runtime-only; osmnx and its geo stack live in
# requirements-ingest.txt and would roughly quadruple the image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -rf /tmp/*.whl

# Fail the BUILD if the extension is unusable.
#
# pyref/engine.py falls back to the pure-Python engine with only a
# warnings.warn when sr_core is missing. That is right for a developer without
# a compiler and completely wrong for a production image: it is a ~20x latency
# regression that nothing surfaces except as slow responses. Catch it here,
# where it is a red build rather than a silent deploy.
RUN python -c "import sr_core; assert hasattr(sr_core, 'Engine'); print('sr_core OK')"

COPY api/ api/
COPY pyref/ pyref/
COPY sim/ sim/
COPY config/ config/
COPY packs.lock .

# Packs are baked in at build time: every region in PACK_REGIONS is fetched
# from the public bucket in packs.lock and verified against its digest, by the
# same api/packs_fetch.py the API runs at startup. The startup fetch then finds
# them present and never touches the network, so a deployed machine boots
# without depending on the bucket, and the image digest pins the packs too.
# The bucket is public-read, so this needs no build secret.
#
# Build with `--build-arg PACK_REGIONS=` to bake nothing and fetch at startup
# instead. Keep the default in step with `[api] regions` in config/config.toml.
# The directory is owned by the app user because a startup fetch writes into
# it as non-root.
ARG PACK_REGIONS=berkeley_oakland,san_francisco
RUN mkdir -p /app/data/packs \
 && if [ -n "$PACK_REGIONS" ]; then \
      python -m api.packs_fetch --regions "$PACK_REGIONS" --dest /app/data/packs; \
    fi \
 && useradd --create-home --uid 10001 app \
 && chown -R app:app /app/data
USER app

EXPOSE 8080

# One process per container, and still no `--workers` — but for one reason
# now, not two. This comment used to give both, and they were never equally
# true.
#
# The rate limiter is no longer one of them. api/ratelimit.py puts the
# Nominatim token bucket in Redis when SR_REDIS_URL is set, so the ~1 req/s
# ceiling holds across every replica instead of per process. Running several
# of these containers behind a load balancer — previously forbidden at any
# count — is now the supported way to add capacity. Without SR_REDIS_URL the
# bucket is per-process, and one replica remains the limit.
#
# If you do put a load balancer in front, also set SR_TRUSTED_PROXIES to the
# number of proxy hops. The routing quota on /route and /reroute keys clients
# by network address, and behind an unconfigured proxy every address is the
# proxy's — which collapses a per-client quota into one bucket that any single
# caller can exhaust for everybody. See ADR-0013's 2026-09-18 amendment.
#
# `--workers N` stays wrong for the reason that was always the real one: the
# C++ search releases the GIL (core/src/bindings.cpp) and api/routes.py is a
# sync handler, so FastAPI runs it in a threadpool and a single process already
# parallelises across every core. Extra workers would buy no throughput and
# would duplicate the loaded graph pack in RAM once per worker.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
