"""Thin Nominatim proxy so the front-end never needs map-data credentials.

Nominatim usage policy compliance: identifying User-Agent, at most ~1
request/second upstream (enforced with a lock + monotonic spacing), and a
small in-process cache so repeated queries are free. The front-end debounces
typing on top of this.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from api.schemas import GeocodeResponse, GeocodeResult

router = APIRouter()

_lock = asyncio.Lock()
_last_call = 0.0
_cache: dict[str, list[GeocodeResult]] = {}
_CACHE_MAX = 512


@router.get("/geocode", response_model=GeocodeResponse)
async def geocode(request: Request, q: str = Query(min_length=2, max_length=200)):
    global _last_call
    cfg = request.app.state.app_state.cfg["api"]
    key = q.strip().lower()
    if key in _cache:
        return GeocodeResponse(results=_cache[key])

    bbox = request.app.state.app_state.pack.meta.get("bbox")
    params = {"q": q, "format": "jsonv2", "limit": 5}
    if bbox:
        west, south, east, north = bbox
        params["viewbox"] = f"{west},{north},{east},{south}"
        params["bounded"] = 1

    async with _lock:
        wait = cfg["nominatim_min_interval_s"] - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # SR_NOMINATIM_CONTACT lets a deployment supply its own
                # contact without editing (or committing) the config file.
                ua = os.environ.get("SR_NOMINATIM_CONTACT",
                                    cfg["nominatim_user_agent"])
                resp = await client.get(
                    cfg["nominatim_url"], params=params,
                    headers={"User-Agent": ua})
        finally:
            _last_call = time.monotonic()

    if resp.status_code != 200:
        raise HTTPException(502, "geocoding service unavailable")
    results = [GeocodeResult(name=item.get("display_name", ""),
                             lat=float(item["lat"]), lon=float(item["lon"]))
               for item in resp.json()]
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = results
    return GeocodeResponse(results=results)
