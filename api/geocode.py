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

Several served packs (ADR-0014 decision 5): the client passes `region`, the
pack its map or GPS fix is in, and the query is bounded to that pack's bbox.
Without `region` on a multi-pack deployment the query goes out unbounded and
the results are filtered to points inside some served bbox. Either way it is
one upstream request against the one global bucket: no per-pack fan-out, and
no union bbox (two metros far apart would bound half a state).
"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from api.ratelimit import LimiterUnavailable, retry_after_header
from api.registry import PackRegistry, bbox_contains
from api.schemas import GeocodeResponse, GeocodeResult

router = APIRouter()

# Per-process, and deliberately left that way. Sharing it would be a second
# Redis round trip to save a Nominatim call that the shared bucket already
# rations; N replicas simply means each warms its own cache and the hit rate
# per replica is lower.
#
# Keyed on (normalized q, effective region). Region None is the multi-pack
# unbounded query, whose post-filtered answer must never be served for a
# region-bounded one, or the other way round.
_cache: dict[tuple[str, str | None], list[GeocodeResult]] = {}
_CACHE_MAX = 512

_RESULT_LIMIT = 5
# The unbounded query is post-filtered, and a worldwide top 5 is usually all
# outside coverage. Asking for more candidates costs the same one token.
_UNBOUNDED_LIMIT = 20


def _effective_region(registry: PackRegistry, region: str | None) -> str | None:
    """The pack whose bbox bounds the query, or None for unbounded.

    An absent `region` on a one-pack deployment means that pack (today's
    behaviour), so it shares a cache entry with `region=<that pack>`. An
    unknown region is the caller's error: 422.
    """
    names = registry.names()
    if region is None:
        return names[0] if len(names) == 1 else None
    if region not in names:
        raise HTTPException(
            422, f"unknown region {region!r}; served regions: {', '.join(names)}")
    return region


@router.get("/geocode", response_model=GeocodeResponse)
async def geocode(request: Request, q: str = Query(min_length=2, max_length=200),
                  region: str | None = Query(default=None, max_length=100)):
    state = request.app.state.app_state
    cfg = state.cfg["api"]
    registry: PackRegistry = state.registry
    # Before the cache and the limiter: a bad region costs neither a token nor
    # an upstream request, and a cached answer never masks the 422.
    effective = _effective_region(registry, region)
    key = (q.strip().lower(), effective)
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

    # A named pack with a null bbox (the sole toy pack) goes out unbounded and
    # unfiltered, exactly as before regions existed.
    bbox = None if effective is None else registry[effective].bbox
    # Annotated because the mixed str/int values otherwise infer as
    # dict[str, object], which httpx's params type does not accept.
    params: dict[str, str | int] = {
        "q": q, "format": "jsonv2",
        "limit": _UNBOUNDED_LIMIT if effective is None else _RESULT_LIMIT}
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
    if effective is None:
        # Unbounded (several packs, no region): keep only what some served
        # pack covers. Disjoint coverage means order is Nominatim's ranking.
        boxes = [registry[n].bbox for n in registry.names()]
        results = [r for r in results
                   if any(bbox_contains(b, (r.lat, r.lon)) for b in boxes)]
        results = results[:_RESULT_LIMIT]
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = results
    return GeocodeResponse(results=results)
