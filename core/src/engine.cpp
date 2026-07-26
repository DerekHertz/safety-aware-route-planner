#include "engine.hpp"

#include <algorithm>
#include <limits>

namespace sr {

namespace {

constexpr double kInf = std::numeric_limits<double>::infinity();

struct HeapEntry {
    double f;
    int32_t e;
    double g;  // payload only — never compared (P2)
};

// std::priority_queue is a max-heap: "a comes after b" comparator yields a
// min-heap on (f, e). Comparing e as tie-break gives the strict total order
// required for cross-implementation determinism (P2).
struct AfterByFEdge {
    bool operator()(const HeapEntry& a, const HeapEntry& b) const {
        if (a.f != b.f) return a.f > b.f;
        return a.e > b.e;
    }
};

}  // namespace

PathOut Engine::shortest_path(
    const double* arc_cost, const double* h,
    const std::vector<std::pair<int32_t, double>>& seeds,
    const std::vector<std::pair<int32_t, double>>& dests) const {
    std::vector<double> dist(static_cast<size_t>(E_), kInf);
    std::vector<int64_t> pred(static_cast<size_t>(E_), -1);

    // dict(dests) semantics: later duplicates overwrite earlier ones
    std::vector<double> dest_adjust(static_cast<size_t>(E_), kInf);  // kInf = "not a dest"
    for (const auto& [d, adj] : dests) dest_adjust[static_cast<size_t>(d)] = adj;

    // P6 slack — uses each LISTED adjust (mirrors the Python loop over the
    // dests list, not the dict), exact even with duplicate dest edges
    double slack = 0.0;
    for (const auto& [d, adj] : dests) {
        const double hd = (h != nullptr) ? h[d] : 0.0;
        const double s = hd - adj;
        if (s > slack) slack = s;
    }

    std::priority_queue<HeapEntry, std::vector<HeapEntry>, AfterByFEdge> heap;
    for (const auto& [e, g0] : seeds) {
        if (g0 < dist[static_cast<size_t>(e)]) {
            dist[static_cast<size_t>(e)] = g0;
            const double he = (h != nullptr) ? h[e] : 0.0;
            const double f = g0 + he;  // P5
            heap.push({f, e, g0});
        }
    }

    double best_final = kInf;
    int32_t best_edge = -1;

    while (!heap.empty()) {
        const HeapEntry top = heap.top();
        heap.pop();
        const double f = top.f;
        const int32_t e = top.e;
        const double g = top.g;
        if (best_edge >= 0 && f > best_final + slack) break;  // P6
        if (g != dist[static_cast<size_t>(e)]) continue;      // P4
        if (dest_adjust[static_cast<size_t>(e)] != kInf) {
            const double final_cost = g + dest_adjust[static_cast<size_t>(e)];  // P5
            if (final_cost < best_final) {
                best_final = final_cost;
                best_edge = e;
            }
        }
        const int32_t lo = turn_ptr_[e];
        const int32_t hi = turn_ptr_[e + 1];
        for (int32_t t = lo; t < hi; ++t) {
            if (!allowed_[t]) continue;
            const int32_t e2 = turn_out_[t];
            const double g_new = g + arc_cost[t];  // P5 (own statement)
            if (g_new < dist[static_cast<size_t>(e2)]) {  // P3
                dist[static_cast<size_t>(e2)] = g_new;
                pred[static_cast<size_t>(e2)] = t;
                const double he2 = (h != nullptr) ? h[e2] : 0.0;
                const double f2 = g_new + he2;  // P5 (own statement)
                heap.push({f2, e2, g_new});
            }
        }
    }

    PathOut out;
    if (best_edge < 0) return out;

    // P7 reconstruction
    out.found = true;
    out.dest_edge = best_edge;
    out.total_cost = best_final;
    int32_t e = best_edge;
    while (pred[static_cast<size_t>(e)] != -1) {
        const int32_t t = static_cast<int32_t>(pred[static_cast<size_t>(e)]);
        out.turns.push_back(t);
        e = turn_in_[t];
    }
    std::reverse(out.turns.begin(), out.turns.end());
    out.first_edge = e;
    return out;
}

}  // namespace sr
