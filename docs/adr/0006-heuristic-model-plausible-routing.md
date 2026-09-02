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
