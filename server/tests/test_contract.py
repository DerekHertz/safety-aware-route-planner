"""The committed OpenAPI file is the only thing the iOS app reads from the service."""

import json
from pathlib import Path

from gapcross.contract import CONTRACT_PATH, render_contract

ENDPOINTS = {
    ("get", "/trips"),
    ("post", "/trips/{trip_id}/default"),
    ("post", "/trips/{trip_id}/comparison"),
    ("post", "/trips/{trip_id}/reroute"),
    ("post", "/drive-reports"),
}

TYPES = {
    "LatLon",
    "Trip",
    "Route",
    "Step",
    "Maneuver",
    "Crossing",
    "Assessment",
    "Evidence",
    "Comparison",
    "ComparisonRequest",
    "RerouteRequest",
    "DriveReport",
    "CrossingReport",
}


def test_committed_contract_matches_the_models():
    committed = Path(CONTRACT_PATH).read_text()
    assert committed == render_contract(), (
        "api/openapi.json is stale: run `uv run python -m gapcross.contract` and commit it"
    )


def test_contract_lists_every_endpoint():
    paths = json.loads(render_contract())["paths"]
    listed = {(method, path) for path, ops in paths.items() for method in ops}
    assert ENDPOINTS <= listed


def test_contract_lists_every_core_type():
    schemas = json.loads(render_contract())["components"]["schemas"]
    assert TYPES <= schemas.keys()


def test_route_failure_is_documented_as_503():
    paths = json.loads(render_contract())["paths"]
    for path in ("/trips/{trip_id}/default", "/trips/{trip_id}/comparison", "/trips/{trip_id}/reroute"):
        assert "503" in paths[path]["post"]["responses"], path


def test_counts_are_never_sent():
    schemas = json.loads(render_contract())["components"]["schemas"]
    for name in ("Route", "Comparison"):
        fields = set(schemas[name]["properties"])
        assert not {f for f in fields if "count" in f}, name
