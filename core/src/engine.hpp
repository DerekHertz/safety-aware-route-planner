// Edge-based Dijkstra / A* — C++17 port of pyref/search.py.
//
// PARITY CONTRACT (mirror of the numbered block in pyref/search.py — any
// change must be made in BOTH files):
//   P1. state = directed edge id; dist[e] = cost having fully traversed e.
//   P2. heap entries (f, edge, g); ordering compares (f, edge) only.
//   P3. strict-improvement relaxation; ties keep incumbent.
//   P4. lazy deletion: on pop, skip iff g != dist[e] (exact equality).
//   P5. only FP ops are single additions, each its own statement (no
//       contraction opportunity; built with /fp:precise).
//   P6. dests finalized when popped; stop when f > best_final + slack,
//       slack = max over dests of (h[d] - adjust[d]) computed at start.
//   P7. pred stores the turn id (-1 for seeds); reconstruction walks
//       pred via turn_in_edge.
#pragma once

#include <cstdint>
#include <queue>
#include <utility>
#include <vector>

namespace sr {

struct PathOut {
    bool found = false;
    std::vector<int32_t> turns;
    int32_t first_edge = -1;
    int32_t dest_edge = -1;
    double total_cost = 0.0;
};

class Engine {
public:
    // Non-owning views of the pack's turn-topology arrays; the Python
    // binding layer keeps the numpy buffers alive for the Engine lifetime.
    Engine(const int32_t* turn_ptr, int64_t num_edges,
           const int32_t* turn_out_edge, const int32_t* turn_in_edge,
           const uint8_t* turn_allowed, int64_t num_turns)
        : turn_ptr_(turn_ptr), turn_out_(turn_out_edge), turn_in_(turn_in_edge),
          allowed_(turn_allowed), E_(num_edges), T_(num_turns) {}

    PathOut shortest_path(
        const double* arc_cost,               // f64[T]
        const double* h,                      // f64[E] or nullptr (Dijkstra)
        const std::vector<std::pair<int32_t, double>>& seeds,
        const std::vector<std::pair<int32_t, double>>& dests) const;

    int64_t num_edges() const { return E_; }
    int64_t num_turns() const { return T_; }

private:
    const int32_t* turn_ptr_;
    const int32_t* turn_out_;
    const int32_t* turn_in_;
    const uint8_t* allowed_;
    int64_t E_;
    int64_t T_;
};

}  // namespace sr
