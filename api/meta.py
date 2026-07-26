"""GET /meta — pack coverage info for the front-end.

Additive endpoint: the frozen /route contract is untouched. The client needs
the pack bbox so it can tell whether a GPS fix falls inside the routable
region and give a useful message instead of a bare 422.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas import MetaResponse

router = APIRouter()


@router.get("/meta", response_model=MetaResponse)
def meta(request: Request) -> MetaResponse:
    state = request.app.state.app_state
    m = state.pack.meta
    return MetaResponse(
        region=m.get("region", "unknown"),
        # Toy/test packs carry no bbox; a null bbox disables coverage checks.
        bbox=m.get("bbox"),
        num_edges=state.pack.num_edges,
    )
