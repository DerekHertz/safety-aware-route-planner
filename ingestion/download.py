"""OSM download via OSMnx v2 (ingestion only — never imported by pyref/api).

Raw graphs are cached as GraphML in data/cache/ so ingestion iterations never
re-hit Overpass; OSMnx's own HTTP response cache is enabled too.
"""
from __future__ import annotations

from pathlib import Path

import networkx as nx

from pyref.config import Config

CACHE_DIR = Path("data") / "cache"


def _configure_osmnx():
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx")
    ox.settings.log_console = False
    ox.settings.requests_timeout = 300
    # Node tags needed for control classification (defaults lack `stop`).
    ox.settings.useful_tags_node = list(
        set(ox.settings.useful_tags_node) | {"highway", "stop", "traffic_signals"}
    )
    return ox


def download_graph(cfg: Config, region: str | None = None) -> nx.MultiDiGraph:
    region = region or cfg.region_name
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    graphml_path = CACHE_DIR / f"{region}.graphml"
    ox = _configure_osmnx()
    if graphml_path.exists():
        print(f"[download] using cached {graphml_path}")
        return ox.load_graphml(graphml_path)
    bbox = cfg.bbox(region)  # (west, south, east, north) == OSMnx v2 (left, bottom, right, top)
    print(f"[download] fetching bbox {bbox} from Overpass ...")
    G = ox.graph_from_bbox(bbox=bbox, network_type=cfg.network_type, simplify=True)
    ox.save_graphml(G, graphml_path)
    print(f"[download] saved {graphml_path} ({len(G.nodes)} nodes, {len(G.edges)} edges)")
    return G
