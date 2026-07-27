"""CLI: download OSM data for the configured region and build the binary pack.

    python -m ingestion.build_pack [--region berkeley_small] [--config path]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ingestion import sanity
from ingestion.download import download_graph, load_approach_controls
from ingestion.pack import build_pack
from pyref.config import DEFAULT_CONFIG_PATH, Config
from pyref.graph import GraphPack


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    ap.add_argument("--region", default=None, help="preset name (default: [region].active)")
    ap.add_argument("--out", default="data/packs", help="output root directory")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    region = args.region or cfg.region_name
    G = download_graph(cfg, region)
    observed = load_approach_controls(cfg, G, region)
    print(f"[build] assembling pack for '{region}' ...")
    pack = build_pack(G, cfg, region, observed_controls=observed)
    out_dir = Path(args.out) / region
    pack.write(out_dir)
    print(f"[build] wrote {out_dir}")

    reloaded = GraphPack.load(out_dir)
    print(sanity.report(reloaded))


if __name__ == "__main__":
    main()
