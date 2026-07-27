"""OSM download via OSMnx v2 (ingestion only — never imported by pyref/api).

Two graphs come out of the same Overpass response:

  * the SIMPLIFIED graph, which becomes the routing graph. OSMnx collapses
    degree-2 interstitial nodes into edge geometry, which is what keeps the
    turn table small.
  * the UNSIMPLIFIED graph, used only by ingestion/approach_controls.py.
    Simplification discards the tags of every node it removes, and stop signs
    and traffic signals live overwhelmingly on those nodes (they are mapped at
    the stop line on each approach arm, not on the junction node). Harvesting
    control from the raw graph is what makes intersection classification
    evidence-based instead of guesswork.

Both calls reuse OSMnx's HTTP response cache, so the second one costs a local
re-parse rather than another Overpass request.

Graphs are cached as GraphML in data/cache/ so ingestion iterations never
re-hit Overpass. The cache filename carries INGEST_SCHEMA_VERSION: bump it
whenever tag retention or simplification changes, or stale caches will keep
silently feeding the old, lossy data into new code.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from ingestion import approach_controls
from pyref.config import Config
from pyref.graph import Control, ControlConfidence

CACHE_DIR = Path("data") / "cache"

# v2: retain give_way/direction/crossing node tags; harvest approach control
# from the unsimplified graph (previously ~87% of stop nodes were discarded).
INGEST_SCHEMA_VERSION = 2

# Node tags needed for control classification. OSMnx defaults are
# {highway, junction, railway, ref} — everything else here has to be asked for.
CONTROL_NODE_TAGS = {
    "highway", "stop", "traffic_signals", "give_way", "crossing",
    "direction", "traffic_signals:direction", "stop:direction",
}


def _configure_osmnx():
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    ox.settings.log_console = False
    ox.settings.requests_timeout = 300
    ox.settings.useful_tags_node = list(
        set(ox.settings.useful_tags_node) | CONTROL_NODE_TAGS
    )
    return ox


def _cache_path(region: str, suffix: str = "") -> Path:
    return CACHE_DIR / f"{region}-v{INGEST_SCHEMA_VERSION}{suffix}.graphml"


def _fetch(cfg: Config, region: str, *, simplify: bool, suffix: str) -> nx.MultiDiGraph:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(region, suffix)
    ox = _configure_osmnx()
    if path.exists():
        print(f"[download] using cached {path}")
        return ox.load_graphml(path)
    bbox = cfg.bbox(region)  # (west, south, east, north) == OSMnx v2 (left, bottom, right, top)
    kind = "simplified" if simplify else "raw"
    print(f"[download] fetching bbox {bbox} from Overpass ({kind}) ...")
    G = ox.graph_from_bbox(bbox=bbox, network_type=cfg.network_type, simplify=simplify)
    ox.save_graphml(G, path)
    print(f"[download] saved {path} ({len(G.nodes)} nodes, {len(G.edges)} edges)")
    return G


def download_graph(cfg: Config, region: str | None = None) -> nx.MultiDiGraph:
    """The simplified routing graph."""
    return _fetch(cfg, region or cfg.region_name, simplify=True, suffix="")


def download_raw_graph(cfg: Config, region: str | None = None) -> nx.MultiDiGraph:
    """The unsimplified graph, with every OSM node and its tags intact."""
    return _fetch(cfg, region or cfg.region_name, simplify=False, suffix="-raw")


# --------------------------------------------------------- approach controls
def _controls_cache_path(region: str) -> Path:
    return CACHE_DIR / f"{region}-v{INGEST_SCHEMA_VERSION}-controls.json"


def load_approach_controls(cfg: Config, G: nx.MultiDiGraph,
                           region: str | None = None
                           ) -> dict[tuple[int, int], approach_controls.ApproachControl]:
    """Observed per-approach control for the junctions of routing graph `G`.

    Cached as JSON keyed by the routing graph's node ids, so re-parsing the
    (much larger) unsimplified graph is a one-time cost per region. Delete the
    file, or bump INGEST_SCHEMA_VERSION, to force a re-harvest.
    """
    region = region or cfg.region_name
    path = _controls_cache_path(region)
    if path.exists():
        print(f"[download] using cached {path}")
        records = json.loads(path.read_text())["approaches"]
        return {
            (int(j), int(f)): approach_controls.ApproachControl(
                Control(c), bool(ms), ControlConfidence(conf))
            for j, f, c, ms, conf in records
        }

    max_offset = float(cfg["ingest"]["max_control_offset_m"])
    print(f"[download] harvesting approach controls (offset <= {max_offset:g} m) ...")
    G_raw = download_raw_graph(cfg, region)
    found = approach_controls.harvest(G_raw, G.nodes, max_offset)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": INGEST_SCHEMA_VERSION,
        "region": region,
        "max_control_offset_m": max_offset,
        "approaches": [[j, f, int(a.control), int(a.must_stop), int(a.confidence)]
                       for (j, f), a in sorted(found.items())],
    }))
    print(f"[download] saved {path} ({len(found):,} observed approaches)")
    return found
