"""The RouteSource and RecordSource seams, their raw types, and the adapters behind them."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from .transport import (
    FixtureMissing,
    FixtureTransport,
    LiveTransport,
    RecordingTransport,
    SourceUnavailable,
    Transport,
)
from .types import LatLon

__all__ = ["FixtureMissing", "LatLon", "SourceUnavailable", "offline_sources", "live_sources"]

USER_AGENT = "gapcross/0.1 (gap-crossing route comparison; research)"
PUBLIC_OSRM = "https://router.project-osrm.org"
PUBLIC_OVERPASS = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 120  # the query itself allows the server 90 s


@dataclass(frozen=True)
class BBox:
    south: float
    west: float
    north: float
    east: float


@dataclass(frozen=True)
class RawIntersection:
    location: LatLon
    bearings: tuple[int, ...]
    entry: tuple[bool, ...]
    in_index: int | None
    out_index: int | None


@dataclass(frozen=True)
class RawStep:
    name: str
    maneuver_type: str
    modifier: str | None
    distance_m: float
    duration_s: float
    location: LatLon
    intersections: tuple[RawIntersection, ...]


@dataclass(frozen=True)
class RawRoute:
    distance_m: float
    duration_s: float
    geometry: tuple[LatLon, ...]
    steps: tuple[RawStep, ...]


@dataclass(frozen=True)
class OsmNode:
    id: int
    location: LatLon
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OsmWay:
    id: int
    tags: dict[str, str]
    node_ids: tuple[int, ...]


@dataclass(frozen=True)
class Restriction:
    id: int
    tags: dict[str, str]
    members: tuple[tuple[str, int, str], ...]  # (type, ref, role)


@dataclass(frozen=True)
class RecordSnapshot:
    nodes: dict[int, OsmNode]
    ways: dict[int, OsmWay]
    restrictions: tuple[Restriction, ...]
    taken_at: datetime | None


class RouteSource(Protocol):
    def route(self, waypoints: list[LatLon]) -> RawRoute:
        """One merged route through the waypoints in order. OSRM has no departure time."""
        ...


class RecordSource(Protocol):
    def snapshot(self, bbox: BBox) -> RecordSnapshot: ...


def _http_json(method: str, url: str, timeout: float = 30, **kwargs: Any) -> dict[str, Any]:
    try:
        response = httpx.request(method, url, headers={"User-Agent": USER_AGENT}, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise SourceUnavailable(f"{method} {url}: {error}") from error


def _latlon(lon_lat: list[float]) -> LatLon:
    return LatLon(lat=lon_lat[1], lon=lon_lat[0])


class OsrmRouteSource:
    def __init__(self, transport: Transport, base_url: str = PUBLIC_OSRM):
        self.transport = transport
        self.base_url = base_url.rstrip("/")

    def route(self, waypoints: list[LatLon]) -> RawRoute:
        points = [[round(p.lat, 6), round(p.lon, 6)] for p in waypoints]
        coords = ";".join(f"{lon},{lat}" for lat, lon in points)
        params = {
            "steps": "true",
            "geometries": "geojson",
            "overview": "full",
            "alternatives": "false",
            "continue_straight": "true",
        }
        url = f"{self.base_url}/route/v1/driving/{coords}"
        body = self.transport.get_json(
            "osrm-route", {"waypoints": points, "params": params},
            lambda: _http_json("GET", url, params=params),
        )
        if body.get("code") != "Ok" or not body.get("routes"):
            raise SourceUnavailable(f"OSRM answered {body.get('code')}: {body.get('message')}")
        return _parse_osrm(body["routes"][0])


def _parse_osrm(route: dict[str, Any]) -> RawRoute:
    """Merge via-point legs into one route: one depart, one arrive, intermediate legs continue."""
    steps: list[RawStep] = []
    legs = route["legs"]
    for leg_index, leg in enumerate(legs):
        for step in leg["steps"]:
            kind = step["maneuver"]["type"]
            if kind == "arrive" and leg_index < len(legs) - 1:
                continue
            if kind == "depart" and leg_index > 0:
                kind = "continue"
            steps.append(RawStep(
                name=step.get("name", ""),
                maneuver_type=kind,
                modifier=step["maneuver"].get("modifier"),
                distance_m=step["distance"],
                duration_s=step["duration"],
                location=_latlon(step["maneuver"]["location"]),
                intersections=tuple(
                    RawIntersection(
                        location=_latlon(i["location"]),
                        bearings=tuple(i.get("bearings", ())),
                        entry=tuple(i.get("entry", ())),
                        in_index=i.get("in"),
                        out_index=i.get("out"),
                    )
                    for i in step.get("intersections", ())
                ),
            ))
    return RawRoute(
        distance_m=route["distance"],
        duration_s=route["duration"],
        geometry=tuple(_latlon(c) for c in route["geometry"]["coordinates"]),
        steps=tuple(steps),
    )


DRIVABLE = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service"


def overpass_query(bbox: BBox) -> str:
    b = f"{bbox.south},{bbox.west},{bbox.north},{bbox.east}"
    return (
        "[out:json][timeout:90];("
        f'way["highway"~"^({DRIVABLE})(_link)?$"]({b});'
        f'node["highway"~"^(stop|give_way|traffic_signals|crossing)$"]({b});'
        f'relation["type"="restriction"]({b});'
        ");out body;>;out body qt;"
    )


class OverpassRecordSource:
    def __init__(self, transport: Transport, url: str = PUBLIC_OVERPASS):
        self.transport = transport
        self.url = url

    def snapshot(self, bbox: BBox) -> RecordSnapshot:
        query = overpass_query(bbox)
        body = self.transport.get_json(
            "overpass", {"query": query},
            lambda: _http_json("POST", self.url, timeout=OVERPASS_TIMEOUT_S, data={"data": query}),
        )
        return _parse_overpass(body)


def _parse_overpass(body: dict[str, Any]) -> RecordSnapshot:
    nodes: dict[int, OsmNode] = {}
    ways: dict[int, OsmWay] = {}
    restrictions: list[Restriction] = []
    for e in body.get("elements", ()):
        tags = e.get("tags", {})
        if e["type"] == "node":
            known = nodes.get(e["id"])
            nodes[e["id"]] = OsmNode(e["id"], LatLon(lat=e["lat"], lon=e["lon"]), tags or (known.tags if known else {}))
        elif e["type"] == "way":
            ways[e["id"]] = OsmWay(e["id"], tags, tuple(e.get("nodes", ())))
        elif e["type"] == "relation" and tags.get("type") == "restriction":
            restrictions.append(Restriction(
                e["id"], tags, tuple((m["type"], m["ref"], m.get("role", "")) for m in e.get("members", ()))
            ))
    stamp = body.get("osm3s", {}).get("timestamp_osm_base")
    taken_at = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
    return RecordSnapshot(nodes, ways, tuple(restrictions), taken_at)


@dataclass(frozen=True)
class Sources:
    routes: RouteSource
    records: RecordSource


def offline_sources() -> Sources:
    transport = FixtureTransport()
    return Sources(OsrmRouteSource(transport), OverpassRecordSource(transport))


def live_sources(record: bool = False, osrm_url: str = PUBLIC_OSRM, overpass_url: str = PUBLIC_OVERPASS) -> Sources:
    transport: Transport = RecordingTransport() if record else LiveTransport()
    return Sources(OsrmRouteSource(transport, osrm_url), OverpassRecordSource(transport, overpass_url))
