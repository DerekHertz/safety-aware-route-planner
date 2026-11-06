# The safety model is heuristic and scenario-validated; base routing is plausible, not competitive

Two related decisions about how good — and how trustworthy — the model has to be, given
this is a craft artifact demonstrating the safety thesis (ADR-0003), not yet a product.

**Safety scoring is heuristic-by-construction, validated by scenarios.** The scores are
expert heuristics (per-type severity, control overrides, tuned weights). "Correct" means
the model flags the maneuvers we say are dangerous in hand-built scenario tests — that
suite *is* the validation contract. We document honestly that the model is **not**
calibrated against real crash/incident data. Empirical calibration is a real thing to
want, but it is a research project of its own and belongs to the product-future line, not
now. Claiming calibration we don't have would undermine the thesis's credibility.

**Base (time-optimal) routing is plausible, not competitive.** We do not chase real-time
traffic or Google-grade ETA accuracy. Deterministic **synthetic traffic** (higher volume
on main roads at commute times) is sufficient to demonstrate the safety tradeoff — and the
same volume signal feeds the time-dynamic component of the busy-road rule (ADR-0005), so
the traffic sim earns its keep twice. Investing in real traffic would be scope-drift
against the thesis.

**Amended 2026-09-24 (ADR-0016): base routing is no longer link time only.** The time
term now includes an expected **control delay** per turn: the wait at a signal, an
all-way stop, or for a gap in conflicting traffic. It is driven by the same synthetic
volume, and it is still deterministic. "Plausible, not competitive" stands. The change
exists because a `fast` route that crosses a busy arterial without a signal to save ten
seconds of driving, then waits a minute for a gap, is not plausible. Real traffic stays
deferred (ADR-0010); see that ADR's 2026-09-24 amendment for how it is now staged.
