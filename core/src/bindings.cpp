// pybind11 module `sr_core`: zero-copy intake of the pack's numpy arrays.
//
// Dtype discipline: arrays must arrive EXACTLY as int32 / uint8 / float64 —
// no silent casting/copies (forcecast would hide a caller bug and break the
// zero-copy + keep_alive lifetime model).
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <stdexcept>
#include <utility>
#include <vector>

#include "engine.hpp"

namespace py = pybind11;

namespace {

template <typename T>
const T* checked_ptr(const py::array& arr, const char* name, int64_t expected_len = -1) {
    if (!py::isinstance<py::array_t<T>>(arr))
        throw std::invalid_argument(std::string(name) + ": wrong dtype");
    auto a = arr.cast<py::array_t<T>>();
    if (a.ndim() != 1 || (a.flags() & py::array::c_style) == 0)
        throw std::invalid_argument(std::string(name) + ": need 1-D contiguous array");
    if (expected_len >= 0 && a.shape(0) != expected_len)
        throw std::invalid_argument(std::string(name) + ": wrong length");
    return a.data();
}

class PyEngine {
public:
    PyEngine(py::array turn_ptr, py::array turn_out_edge,
             py::array turn_in_edge, py::array turn_allowed)
        : turn_ptr_(std::move(turn_ptr)), turn_out_(std::move(turn_out_edge)),
          turn_in_(std::move(turn_in_edge)), allowed_(std::move(turn_allowed)) {
        const int64_t e_plus_1 = turn_ptr_.shape(0);
        if (e_plus_1 < 1) throw std::invalid_argument("turn_ptr: empty");
        const int64_t E = e_plus_1 - 1;
        const int64_t T = turn_out_.shape(0);
        engine_ = std::make_unique<sr::Engine>(
            checked_ptr<int32_t>(turn_ptr_, "turn_ptr"),
            E,
            checked_ptr<int32_t>(turn_out_, "turn_out_edge", T),
            checked_ptr<int32_t>(turn_in_, "turn_in_edge", T),
            checked_ptr<uint8_t>(allowed_, "turn_allowed", T),
            T);
    }

    py::object shortest_path(py::array arc_cost, py::object h_obj,
                             std::vector<std::pair<int32_t, double>> seeds,
                             std::vector<std::pair<int32_t, double>> dests) {
        const double* arc = checked_ptr<double>(arc_cost, "arc_cost", engine_->num_turns());
        const double* h = nullptr;
        py::array h_arr;  // keep alive through the call
        if (!h_obj.is_none()) {
            h_arr = h_obj.cast<py::array>();
            h = checked_ptr<double>(h_arr, "h", engine_->num_edges());
        }
        sr::PathOut out;
        {
            py::gil_scoped_release release;
            out = engine_->shortest_path(arc, h, seeds, dests);
        }
        if (!out.found) return py::none();
        py::array_t<int32_t> turns(static_cast<py::ssize_t>(out.turns.size()));
        std::copy(out.turns.begin(), out.turns.end(), turns.mutable_data());
        return py::make_tuple(turns, out.first_edge, out.dest_edge, out.total_cost);
    }

private:
    py::array turn_ptr_, turn_out_, turn_in_, allowed_;
    std::unique_ptr<sr::Engine> engine_;
};

}  // namespace

PYBIND11_MODULE(sr_core, m) {
    m.doc() = "Safety-aware route planner C++17 core (parity twin of pyref/search.py)";
    py::class_<PyEngine>(m, "Engine")
        .def(py::init<py::array, py::array, py::array, py::array>(),
             py::arg("turn_ptr"), py::arg("turn_out_edge"),
             py::arg("turn_in_edge"), py::arg("turn_allowed"))
        .def("shortest_path", &PyEngine::shortest_path,
             py::arg("arc_cost"), py::arg("h"),
             py::arg("seeds"), py::arg("dests"));
}
