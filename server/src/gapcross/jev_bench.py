"""Measure Jev latency per batch on synthetic but realistic evidence built from the recorded Overpass snapshot.

    uv run python -m gapcross.jev_bench [--sizes 5,5,10,10,15,15] [--record] [--model jev-latest]

Makes live calls (a handful), straight to Jev with no cache, so every call measures the API.
Reads TYPESAFE_API_KEY from the environment by name. With --record, each request/response pair is also
written to server/fixtures/ for offline tests. The evidence layout here is a stand-in for what S3 will build.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from .jev import KINDS, DEFAULT_MODEL, OpenCrossing, build_question, live_jev, question_name
from .sources import BBox, RecordSnapshot, offline_sources

TAGS = ("name", "highway", "maxspeed", "lanes", "oneway", "surface", "sidewalk")
CONTROL = ("highway", "traffic_signals", "stop", "direction", "crossing")
OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _pick(tags: dict[str, str], wanted: tuple[str, ...]) -> dict[str, str]:
    return {k: tags[k] for k in wanted if k in tags}


def sacramento_crossings(snapshot: RecordSnapshot, main_name: str = "Sacramento Street") -> list[OpenCrossing]:
    """One open crossing per junction where a named street meets the main street, with its OSM records."""
    by_node: dict[int, list] = {}
    for way in snapshot.ways.values():
        for node_id in way.node_ids:
            by_node.setdefault(node_id, []).append(way)
    out: list[OpenCrossing] = []
    demo = ("Oregon Street", "Ward Street")  # the two demo crossings come first
    for node_id, ways in sorted(by_node.items(), key=lambda kv: (not any(
            w.tags.get("name") in demo for w in kv[1]), kv[0])):
        mains = [w for w in ways if w.tags.get("name") == main_name]
        sides = [w for w in ways if w.tags.get("name") and w.tags.get("name") != main_name]
        if not mains or not sides:
            continue
        node = snapshot.nodes[node_id]
        side = sides[0]
        key = f"{node_id}/{'straight' if len(out) % 2 == 0 else 'left_onto'}/{OCTANTS[len(out) % 8]}"
        evidence = {
            "main_street": _pick(mains[0].tags, TAGS),
            "cross_street": _pick(side.tags, TAGS),
            "junction_node": {"osm_id": node_id, "tags": _pick(node.tags, CONTROL), "lat": node.location.lat,
                              "lon": node.location.lon},
            "approach": {"kind": key.split("/")[1], "bearing_octant": key.split("/")[2]},
            "osm_snapshot": snapshot.taken_at.isoformat() if snapshot.taken_at else None,
        }
        out.append(OpenCrossing(key, evidence))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="5,5,10,10,15,15", help="crossings per call, one call each")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    trip = json.loads((Path(__file__).resolve().parents[2] / "trips.json").read_text())["trips"][0]
    snapshot = offline_sources().records.snapshot(BBox(**trip["bbox"]))
    pool = sacramento_crossings(snapshot)
    print(f"{len(pool)} Sacramento Street junctions with a named cross street in the recorded snapshot")
    jev = live_jev(record=args.record, model=args.model)
    latencies: list[float] = []
    for size in (int(s) for s in args.sizes.split(",")):
        batch = pool[:size]
        state = {"crossings": {c.key: dict(c.evidence) for c in batch}}
        questions = {question_name(c.key, k): build_question(c.key, k) for c in batch for k in KINDS}
        started = time.perf_counter()
        answers, version = jev.ask(state, questions)
        latency = time.perf_counter() - started
        latencies.append(latency)
        confident = sum(1 for a in answers.values() if a.probability >= 0.9)
        print(f"crossings={len(batch)} questions={len(questions)} latency={latency:.2f}s model={version} "
              f"answers>=0.9: {confident}/{len(answers)}")
    ordered = sorted(latencies)
    print(f"calls={len(latencies)} p50={statistics.median(ordered):.2f}s max={ordered[-1]:.2f}s min={ordered[0]:.2f}s")


if __name__ == "__main__":
    main()
