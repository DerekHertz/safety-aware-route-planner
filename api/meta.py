"""GET /meta — pack coverage info for the front-end.

Additive endpoint: the frozen /route contract is untouched. The client needs
the served bboxes so it can tell whether a point falls inside the routable
coverage, choose an initial view, and refuse a cross-region request before
calling /route (ADR-0014 decision 6).

`packs` lists every served pack in `[api] regions` order; the first is the
default. The top-level fields repeat the default pack so a client that
predates `packs` keeps working.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.registry import PackRegistry
from api.schemas import MetaResponse, ServedPack

router = APIRouter()


@router.get("/meta", response_model=MetaResponse)
def meta(request: Request) -> MetaResponse:
    registry: PackRegistry = request.app.state.app_state.registry
    packs = []
    for name in registry.names():
        entry = registry[name]
        packs.append(ServedPack(
            region=name,
            # Toy/test packs carry no bbox; a null bbox disables coverage checks.
            bbox=None if entry.bbox is None else list(entry.bbox),
            num_edges=entry.pack.num_edges,
        ))
    default = packs[0]
    return MetaResponse(region=default.region, bbox=default.bbox,
                        num_edges=default.num_edges, packs=packs)
