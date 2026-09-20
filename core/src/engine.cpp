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

// P8 scratch. dist/pred/dest_adjust are O(E) and were allocated fresh on
// every call; a request runs 4-6 searches, so that was 4-6 x O(E) of malloc +
// first-touch page faults per request. The buffers are reused instead, reset
// to exactly the values a fresh allocation would have held.
//
// THREAD SAFETY: `thread_local`, never a member of Engine. api/routes.py
// declares its handlers `def`, so FastAPI runs them in a worker threadpool,
// and PyEngine::shortest_path releases the GIL around this function — several
// requests really are inside one const Engine at once. Scratch on the Engine
// would be a data race; scratch on the stack is what we are trying to avoid;
// per-thread is both race-free and free of any locking in the hot path. Cost:
// one buffer set resident per worker thread that has ever routed (see the
// memory note in pyref/search.py).
//
// RESET SEMANTICS: reset happens at the START of a search, from the scratch's
// own record of what it dirtied, so an early `return` can never leave a
// poisoned buffer for the next caller. dist/pred are refilled wholesale
// (the settled set is ~half of E, so tracking it is not worth the
// bookkeeping); dest_adjust is reset only at the handful of indices the
// previous call wrote, because the dests list is tiny.
struct Scratch {
    std::vector<double> dist;
    std::vector<int64_t> pred;
    std::vector<double> dest_adjust;
    std::vector<int32_t> dirty_dests;

    void begin(size_t E) {
        if (dist.size() != E) {  // first use, or a different pack
            dist.assign(E, kInf);
            pred.assign(E, -1);
            dest_adjust.assign(E, kInf);
            dirty_dests.clear();
            return;
        }
        std::fill(dist.begin(), dist.end(), kInf);
        std::fill(pred.begin(), pred.end(), -1);
        for (const int32_t d : dirty_dests)
            dest_adjust[static_cast<size_t>(d)] = kInf;
        dirty_dests.clear();
    }
};

}  // namespace

PathOut Engine::shortest_path(
    const double* arc_cost, const double* h,
    const std::vector<std::pair<int32_t, double>>& seeds,
    const std::vector<std::pair<int32_t, double>>& dests) const {
    thread_local Scratch scratch;  // P8 — see the Scratch comment above
    scratch.begin(static_cast<size_t>(E_));
    std::vector<double>& dist = scratch.dist;
    std::vector<int64_t>& pred = scratch.pred;

    // dict(dests) semantics: later duplicates overwrite earlier ones
    std::vector<double>& dest_adjust = scratch.dest_adjust;  // kInf = "not a dest"
    for (const auto& [d, adj] : dests) {
        dest_adjust[static_cast<size_t>(d)] = adj;
        scratch.dirty_dests.push_back(d);  // reset only these next time
    }

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
