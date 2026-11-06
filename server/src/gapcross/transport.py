"""How adapters reach the outside world: live HTTP, recorded fixtures, or live-and-record.

Fixtures are keyed by the service name and the request's meaning (waypoints, a query), not by
URL, so a recording from the public OSRM server also answers for a self-hosted one.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures"

Fetch = Callable[[], dict[str, Any]]


class SourceUnavailable(Exception):
    """A provider could not answer. Becomes check_failed or an incomplete search, never unknown."""


class FixtureMissing(Exception):
    """A test asked for a request nobody recorded. Record it; never fall back to the network."""


class Transport(Protocol):
    def get_json(self, service: str, request: dict[str, Any], fetch: Fetch) -> dict[str, Any]: ...


def fixture_path(service: str, request: dict[str, Any], directory: Path = FIXTURE_DIR) -> Path:
    canonical = json.dumps({"service": service, "request": request}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return directory / f"{service}-{digest}.json"


class LiveTransport:
    def get_json(self, service: str, request: dict[str, Any], fetch: Fetch) -> dict[str, Any]:
        return fetch()


class FixtureTransport:
    def __init__(self, directory: Path = FIXTURE_DIR):
        self.directory = directory

    def get_json(self, service: str, request: dict[str, Any], fetch: Fetch) -> dict[str, Any]:
        path = fixture_path(service, request, self.directory)
        if not path.exists():
            raise FixtureMissing(f"no fixture {path.name} for {service} request {json.dumps(request)}")
        return json.loads(path.read_text())["response"]


class RecordingTransport:
    def __init__(self, directory: Path = FIXTURE_DIR):
        self.directory = directory

    def get_json(self, service: str, request: dict[str, Any], fetch: Fetch) -> dict[str, Any]:
        response = fetch()
        path = fixture_path(service, request, self.directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"request": request, "response": response}, indent=1, sort_keys=True) + "\n")
        return response
