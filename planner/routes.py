"""The route service, reached only over HTTP `/route` (ADR-0019 decision 1)."""
from __future__ import annotations

import os
from typing import Any, Protocol

import httpx


class RouteServiceError(Exception):
    """`/route` refused or failed. `status` is what the planner answers with:
    the route service's own 4xx (a coverage 422 is the user's to fix), or 502."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class RouteClient(Protocol):
    async def route(self, body: dict[str, Any],
                    forwarded_for: str | None = None) -> list[dict[str, Any]]: ...


class HttpRouteClient:
    def __init__(self, base_url: str | None = None, timeout_s: float = 20.0,
                 client: httpx.AsyncClient | None = None) -> None:
        self.base_url = (base_url or os.environ.get("SR_PLANNER_ROUTE_URL")
                         or "http://localhost:8000").rstrip("/")
        self.timeout_s = timeout_s
        self._client = client or httpx.AsyncClient()

    async def route(self, body: dict[str, Any],
                    forwarded_for: str | None = None) -> list[dict[str, Any]]:
        # The route service keys its quota by client. Every planner call comes
        # from this one process, so pass the user's address on; it is read only
        # where the deployment sets SR_TRUSTED_PROXIES to count this hop.
        headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
        try:
            resp = await self._client.post(f"{self.base_url}/route", json=body,
                                           headers=headers, timeout=self.timeout_s)
        except httpx.HTTPError as exc:
            raise RouteServiceError(502, "the route service is unreachable") from exc
        if resp.status_code != 200:
            detail = f"the route service answered {resp.status_code}"
            try:
                got = resp.json().get("detail")
                if isinstance(got, str):
                    detail = got
            except (ValueError, AttributeError):
                pass
            status = resp.status_code if 400 <= resp.status_code < 500 else 502
            raise RouteServiceError(status, detail)
        routes = resp.json().get("routes")
        if not isinstance(routes, list) or not routes:
            raise RouteServiceError(502, "the route service returned no routes")
        return routes

    async def aclose(self) -> None:
        await self._client.aclose()
