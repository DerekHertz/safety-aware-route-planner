"""Scratch-buffer reuse must be observationally identical to fresh allocation.

Both engines reuse per-thread `dist` / `pred` (and, in C++, `dest_adjust`)
buffers across searches instead of allocating `O(E)` on every call. That is
only safe if two properties hold, and neither is provable by a test that runs
a single search:

  1. **No stale state leaks between searches.** A search that ends on the P6
     early break leaves the frontier dirty by construction, so the reset has
     to be complete. Every test here runs a *sequence* of searches on one
     engine and compares each answer against the same query run on a **cold**
     buffer — a fresh thread, hence a freshly allocated scratch.
  2. **No data race.** `api/routes.py` declares its handlers `def`, so FastAPI
     runs them in a worker threadpool and several requests can be inside the
     router at once; `sr_core` additionally releases the GIL for the duration
     of the search. Scratch hung off the shared `Router`/`Engine` would be a
     correctness bug, not a perf detail. The concurrency tests hammer one
     shared engine from several threads and demand bitwise-identical answers.

These tests are deliberately stronger than "the route looks right": they
compare the full (turn ids, first edge, dest edge, cost) tuple with `==`, so a
single flipped predecessor or a cost off by one ulp fails.
"""
from __future__ import annotations

import threading

import numpy as np
import pytest

from pyref.costs import arc_cost, heuristic
from pyref.graph import RoadClass
from pyref.search import shortest_path, topo_of
from tests.helpers.fixtures import make_costs
from tests.helpers.toy_graphs import GraphBuilder

try:  # the C++ twin is optional locally, enforced in CI
    import sr_core
except ImportError:  # pragma: no cover - exercised on machines without it
    sr_core = None

ENGINES = ["pyref"] + (["cpp"] if sr_core is not None else [])

_SPACING_DEG = 0.008
LAMBDAS = [0.0, 0.5, 1.5]


def _grid(n: int):
    """An n x n two-way residential grid — big enough that a corrupted buffer
    actually changes an answer, unlike the 3x3 toy."""
    b = GraphBuilder()
    nodes = [[b.node(_SPACING_DEG * r, _SPACING_DEG * c) for c in range(n)]
             for r in range(n)]
    for r in range(n):
        for c in range(n):
            if c + 1 < n:
                b.edge(nodes[r][c], nodes[r][c + 1], length_m=1000.0)
            if r + 1 < n:
                # a couple of faster rows so the A* heuristic is genuinely
                # slack and searches end on the P6 break with a dirty frontier
                rc = RoadClass.primary if r % 3 == 0 else RoadClass.residential
                b.edge(nodes[r][c], nodes[r + 1][c], length_m=1000.0,
                       road_class=rc, speed_kph=56.0 if r % 3 == 0 else 36.0)
    return b.build()


def _queries(pack, qc, n=8, seed=7):
    """(arc_cost, h, seeds, dests) tuples over random node pairs, cycling the
    lambda sweep and alternating Dijkstra / A* so both P6 paths are covered."""
    rng = np.random.default_rng(seed)
    out = []
    attempts = 0
    while len(out) < n and attempts < 20 * n:
        attempts += 1
        u, v = (int(x) for x in rng.integers(0, pack.num_nodes, size=2))
        if u == v:
            continue
        seeds = [(int(e), float(qc.edge_time_s[e]))
                 for e in np.flatnonzero(pack.edge_tail == u)]
        dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == v)]
        if not seeds or not dests:
            continue
        lam = LAMBDAS[len(out) % len(LAMBDAS)]
        ac = arc_cost(pack, qc, lam)
        h = None
        if len(out) % 2:
            h = heuristic(pack, qc, float(pack.node_lat[v]),
                          float(pack.node_lon[v]))
        out.append((ac, h, seeds, dests))
    assert len(out) == n
    return out


def _norm(res):
    """One comparable tuple for either engine's return shape."""
    if res is None:
        return None
    if isinstance(res, tuple):  # sr_core returns (turns, first, dest, cost)
        turns, first, dest, cost = res
        return ([int(t) for t in turns], int(first), int(dest), float(cost))
    return ([int(t) for t in res.turn_ids], int(res.first_edge),
            int(res.dest_edge), float(res.total_cost))


def _make_runner(engine_name, pack):
    if engine_name == "cpp":
        eng = sr_core.Engine(pack.turn_ptr, pack.turn_out_edge,
                             pack.turn_in_edge, pack.turn_allowed)
        return lambda ac, h, s, d: _norm(eng.shortest_path(ac, h, s, d))
    topo = topo_of(pack)
    return lambda ac, h, s, d: _norm(shortest_path(topo, ac, h, s, d))


def _in_fresh_thread(fn):
    """Run `fn` on a brand-new OS thread and return its value.

    A new thread means freshly allocated thread-local scratch, i.e. exactly
    the pre-reuse `np.full` / `std::vector(E, kInf)` behaviour. This is the
    oracle the hot path is measured against.
    """
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # pragma: no cover - surfaced below
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def _cold(engine_name, pack, query):
    """The answer to one query on a never-used buffer AND a fresh engine."""
    return _in_fresh_thread(lambda: _make_runner(engine_name, pack)(*query))


@pytest.fixture(scope="module")
def grid_pack():
    return _grid(8)


@pytest.mark.parametrize("engine_name", ENGINES)
def test_sequential_searches_match_cold_buffers(engine_name, grid_pack):
    """Run A, then B, then C... on ONE engine; each must equal its cold run.

    This is the test that a single-search test cannot be: a reset that misses
    the edges settled by the previous query shows up here as a wrong
    predecessor chain or a cost inherited from the wrong lambda.
    """
    pack = grid_pack
    qc = make_costs(pack)
    queries = _queries(pack, qc, n=9)
    expected = [_cold(engine_name, pack, q) for q in queries]
    assert any(e is not None for e in expected)

    run = _make_runner(engine_name, pack)
    assert [run(*q) for q in queries] == expected
    # ...and again in the opposite order, so no result depends on which query
    # happened to dirty the buffer first.
    assert [run(*q) for q in reversed(queries)] == list(reversed(expected))
    # ...and a third pass, to catch a reset that is only correct once.
    assert [run(*q) for q in queries] == expected


@pytest.mark.parametrize("engine_name", ENGINES)
def test_unreachable_after_reachable_still_unreachable(engine_name, grid_pack):
    """The nastiest stale-state shape: a query with NO answer, run straight
    after one that filled the buffer. A leftover `dist` entry from the
    previous search can make an isolated seed look relaxed."""
    pack = grid_pack
    qc = make_costs(pack)
    ac = arc_cost(pack, qc, 0.0)
    seeds = [(0, float(qc.edge_time_s[0]))]

    reachable = _queries(pack, qc, n=1)[0]
    run = _make_runner(engine_name, pack)
    assert run(*reachable) is not None
    assert run(ac, None, seeds, []) is None


@pytest.mark.parametrize("engine_name", ENGINES)
def test_concurrent_searches_on_one_engine_are_race_free(engine_name, grid_pack):
    """Several threads inside one engine at once — the FastAPI threadpool
    shape. `sr_core` releases the GIL here, so shared mutable scratch corrupts
    for real rather than theoretically."""
    pack = grid_pack
    qc = make_costs(pack)
    queries = _queries(pack, qc, n=6)
    expected = [_cold(engine_name, pack, q) for q in queries]

    run = _make_runner(engine_name, pack)   # ONE engine, shared by all threads
    barrier = threading.Barrier(4)
    failures: list[str] = []

    def worker(tid: int):
        barrier.wait()
        for _ in range(12):
            for i, q in enumerate(queries):
                got = run(*q)
                if got != expected[i]:
                    failures.append(f"thread {tid} query {i}: {got!r}")
                    return

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failures


@pytest.mark.parametrize("engine_name", ENGINES)
def test_two_packs_on_one_thread(engine_name):
    """Different graphs interleaved on one thread: the scratch has to resize,
    and the smaller pack must not read the larger pack's leftovers."""
    small = _grid(3)
    big = _grid(7)
    qc_small = make_costs(small)
    qc_big = make_costs(big)
    q_small = _queries(small, qc_small, n=3, seed=11)
    q_big = _queries(big, qc_big, n=3, seed=13)
    exp_small = [_cold(engine_name, small, q) for q in q_small]
    exp_big = [_cold(engine_name, big, q) for q in q_big]

    run_small = _make_runner(engine_name, small)
    run_big = _make_runner(engine_name, big)
    for i in range(3):
        assert run_small(*q_small[i]) == exp_small[i]
        assert run_big(*q_big[i]) == exp_big[i]
        assert run_small(*q_small[i]) == exp_small[i]


@pytest.mark.skipif(sr_core is None, reason="sr_core extension not built")
def test_engines_still_agree_across_a_sequence(grid_pack):
    """Parity, but across a *sequence* — tests/test_parity_cpp.py builds a
    fresh engine per assertion, so it cannot see a reuse divergence."""
    pack = grid_pack
    qc = make_costs(pack)
    queries = _queries(pack, qc, n=9)
    run_py = _make_runner("pyref", pack)
    run_cpp = _make_runner("cpp", pack)
    for q in queries:
        assert run_cpp(*q) == run_py(*q)   # bitwise: floats compared with ==
