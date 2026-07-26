"""Benchmark: pyref vs C++ engine on random OD pairs over the active pack.

    .venv/Scripts/python.exe scripts/bench.py [--pack data/packs/berkeley_oakland] [-n 50]
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from pyref.config import Config
from pyref.costs import arc_cost, compute_costs, heuristic
from pyref.graph import GraphPack
from pyref.search import shortest_path, topo_of
from sim.snapshot import free_flow


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="data/packs/berkeley_oakland")
    ap.add_argument("-n", type=int, default=50)
    args = ap.parse_args()

    cfg = Config.load()
    pack = GraphPack.load(args.pack)
    qc = compute_costs(pack, free_flow(pack, cfg), cfg)
    topo = topo_of(pack)
    ac = arc_cost(pack, qc, 1.5)

    try:
        import sr_core
        cpp = sr_core.Engine(pack.turn_ptr, pack.turn_out_edge,
                             pack.turn_in_edge, pack.turn_allowed)
    except ImportError:
        cpp = None
        print("sr_core not built - benchmarking pyref only")

    rng = np.random.default_rng(7)
    queries = []
    for _ in range(args.n):
        u, v = (int(x) for x in rng.integers(0, pack.num_nodes, size=2))
        seeds = [(int(e), float(qc.edge_time_s[e]))
                 for e in np.flatnonzero(pack.edge_tail == u)]
        dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == v)]
        if not seeds or not dests:
            continue
        h = heuristic(pack, qc, float(pack.node_lat[v]), float(pack.node_lon[v]))
        queries.append((seeds, dests, h))

    def bench(run):
        times = []
        found = 0
        for seeds, dests, h in queries:
            t0 = time.perf_counter()
            r = run(seeds, dests, h)
            times.append(time.perf_counter() - t0)
            if r is not None:
                found += 1
        return times, found

    t_py, found_py = bench(lambda s, d, h: shortest_path(topo, ac, h, s, d))
    print(f"pyref : {len(queries)} queries, {found_py} routed, "
          f"median {statistics.median(t_py)*1000:.1f} ms, "
          f"mean {statistics.mean(t_py)*1000:.1f} ms")
    if cpp is not None:
        t_cpp, found_cpp = bench(lambda s, d, h: cpp.shortest_path(ac, h, s, d))
        assert found_cpp == found_py
        print(f"sr_core: {len(queries)} queries, {found_cpp} routed, "
              f"median {statistics.median(t_cpp)*1000:.2f} ms, "
              f"mean {statistics.mean(t_cpp)*1000:.2f} ms")
        print(f"speedup: {statistics.median(t_py)/statistics.median(t_cpp):.1f}x (median)")


if __name__ == "__main__":
    main()
