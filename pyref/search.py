"""Edge-based Dijkstra / A* — the pure-Python PARITY REFERENCE.

The C++ engine (core/src/engine.cpp) must mirror this file exactly. The
numbered rules below are the parity contract; any change here must be made
in both places.

PARITY CONTRACT
  P1. Search state = directed edge id. dist[e] = generalized cost of having
      fully traversed e. Seeds initialize dist directly.
  P2. Heap entries are (f, edge, g); ordering compares (f, edge) only,
      lexicographically — a strict total order, so pop order is identical
      for ANY correct binary min-heap. g is payload, never compared.
      (Strict-improvement pushes make duplicate (f, edge) keys impossible.)
  P3. Relaxation is strict: push iff g_new < dist[e2]; ties keep incumbent.
  P4. Lazy deletion: on pop, skip iff g != dist[e] (exact float equality is
      safe — dist[e] was assigned from the very value that was pushed).
  P5. The only floating-point operations are single additions, each in its
      own statement (no fused/reassociated expressions):
          g_new = g + arc_cost[t]
          f     = g_new + h[e2]          (A* only; h omitted for Dijkstra)
          final = g + dest_adjust[e]
  P6. Destination edges are finalized when POPPED (settled). Termination:
      stop when popped f > best_final + slack, where
          slack = max over dest edges d of (h[d] - dest_adjust[d]),  >= 0,
      computed once at start. dest_adjust <= 0 (unused tail refund) and
      h[d] > 0 make plain `f >= best_final` inexact; the fixed slack bound
      is exact, deterministic, and costs at most a few extra pops.
  P7. pred stores the TURN id taken to reach an edge (-1 for seeds);
      reconstruction walks pred via turn_in_edge inside the engine.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

INF = float("inf")


class TurnTopo(NamedTuple):
    """Static topology arrays — identical inputs for both engines."""
    turn_ptr: np.ndarray       # i32[E+1]
    turn_out_edge: np.ndarray  # i32[T]
    turn_in_edge: np.ndarray   # i32[T]
    turn_allowed: np.ndarray   # u8[T]


@dataclass(frozen=True)
class PathResult:
    turn_ids: np.ndarray   # i32[K] turns taken, in order
    first_edge: int
    dest_edge: int
    total_cost: float      # dist[dest_edge] + dest_adjust[dest_edge]

    def edges(self, turn_out_edge: np.ndarray) -> list[int]:
        return [self.first_edge] + [int(turn_out_edge[t]) for t in self.turn_ids]


def topo_of(pack) -> TurnTopo:
    return TurnTopo(pack.turn_ptr, pack.turn_out_edge,
                    pack.turn_in_edge, pack.turn_allowed)


def shortest_path(topo: TurnTopo,
                  arc_cost: np.ndarray,
                  h: np.ndarray | None,
                  seeds: list[tuple[int, float]],
                  dests: list[tuple[int, float]]) -> PathResult | None:
    turn_ptr = topo.turn_ptr
    turn_out_edge = topo.turn_out_edge
    turn_allowed = topo.turn_allowed
    E = len(turn_ptr) - 1

    dist = np.full(E, INF, dtype=np.float64)
    pred = np.full(E, -1, dtype=np.int64)

    dest_adjust = dict(dests)
    # P6 termination slack
    slack = 0.0
    for d, adj in dests:
        s = (h[d] if h is not None else 0.0) - adj
        if s > slack:
            slack = s

    heap: list[tuple[float, int, float]] = []
    for e, g0 in seeds:
        if g0 < dist[e]:
            dist[e] = g0
            f = g0 + (h[e] if h is not None else 0.0)
            heapq.heappush(heap, (f, e, g0))

    best_final = INF
    best_edge = -1

    while heap:
        f, e, g = heapq.heappop(heap)
        if best_edge >= 0 and f > best_final + slack:   # P6
            break
        if g != dist[e]:                                # P4
            continue
        if e in dest_adjust:
            final = g + dest_adjust[e]                  # P5
            if final < best_final:
                best_final = final
                best_edge = e
        lo = turn_ptr[e]
        hi = turn_ptr[e + 1]
        for t in range(lo, hi):
            if not turn_allowed[t]:
                continue
            e2 = int(turn_out_edge[t])
            g_new = g + arc_cost[t]                     # P5
            if g_new < dist[e2]:                        # P3
                dist[e2] = g_new
                pred[e2] = t
                f2 = g_new + (h[e2] if h is not None else 0.0)  # P5
                heapq.heappush(heap, (f2, e2, g_new))

    if best_edge < 0:
        return None

    # P7 reconstruction
    turn_in_edge = topo.turn_in_edge
    turns: list[int] = []
    e = best_edge
    while pred[e] != -1:
        t = int(pred[e])
        turns.append(t)
        e = int(turn_in_edge[t])
    turns.reverse()
    return PathResult(turn_ids=np.asarray(turns, dtype=np.int32),
                      first_edge=e, dest_edge=best_edge,
                      total_cost=best_final)
