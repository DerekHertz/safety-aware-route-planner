"""Thin Nominatim proxy so the front-end never needs map-data credentials.

Nominatim usage policy compliance: identifying User-Agent, and at most ~1
request/second upstream. That ceiling is enforced by a token bucket in
api/ratelimit.py, which is shared across replicas when Redis is configured —
the ~1/s is a property of the deployment, not of this process. A small
in-process cache makes repeated queries free, and the front-end debounces
typing on top of both.

Over budget this returns 429, it does not wait. The lock-and-sleep version this
replaced serialized callers, so N concurrent typists each waited N seconds
while holding a connection; refusing a keystroke whose suggestions nobody was
going to read is cheaper for everyone. See ADR-0013.
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from api.ratelimit import LimiterUnavailable, retry_after_header
from api.schemas import GeocodeResponse, GeocodeResult

router = APIRouter()

# Per-process, and deliberately left that way. Sharing it would be a second
# Redis round trip to save a Nominatim call that the shared bucket already
# rations; N replicas simply means each warms its own cache and the hit rate
# per replica is lower.
_cache: dict[str, list[GeocodeResult]] = {}
_CACHE_MAX = 512


@router.get("/geocode", response_model=GeocodeResponse)
async def geocode(request: Request, q: str = Query(min_length=2, max_length=200)):
    state = request.app.state.app_state
    cfg = state.cfg["api"]
    key = q.strip().lower()
    # Before the limiter: a cache hit costs Nominatim nothing, so spending a
    # token on it would refuse a real lookup to serve a free one.
    if key in _cache:
        return GeocodeResponse(results=_cache[key])

    try:
        retry_after = await state.limiter.acquire()
    except LimiterUnavailable as exc:
        # Fail closed. The reasoning — why serving anyway is worse than a brief
        # outage — is on RedisTokenBucket.acquire. 503 rather than 429 because
        # the caller did nothing wrong; our dependency did.
        raise HTTPException(
            503, "geocoding is temporarily unavailable",
            headers={"Retry-After": "5"}) from exc
    if retry_after is not None:
        raise HTTPException(
            429, "too many geocoding requests",
            headers={"Retry-After": retry_after_header(retry_after)})

    bbox = state.registry.only().pack.meta.get("bbox")
    # Annotated because the mixed str/int values otherwise infer as
    # dict[str, object], which httpx's params type does not accept.
    params: dict[str, str | int] = {"q": q, "format": "jsonv2", "limit": 5}
    if bbox:
        west, south, east, north = bbox
        params["viewbox"] = f"{west},{north},{east},{south}"
        params["bounded"] = 1

    async with httpx.AsyncClient(timeout=10) as client:
        # SR_NOMINATIM_CONTACT lets a deployment supply its own contact
        # without editing (or committing) the config file.
        ua = os.environ.get("SR_NOMINATIM_CONTACT", cfg["nominatim_user_agent"])
        resp = await client.get(cfg["nominatim_url"], params=params,
                                headers={"User-Agent": ua})

    if resp.status_code != 200:
        raise HTTPException(502, "geocoding service unavailable")
    results = [GeocodeResult(name=item.get("display_name", ""),
                             lat=float(item["lat"]), lon=float(item["lon"]))
               for item in resp.json()]
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = results
    return GeocodeResponse(results=results)
