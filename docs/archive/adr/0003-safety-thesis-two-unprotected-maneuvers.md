# The safety thesis: penalize two unprotected maneuvers, on top of time-optimal routing

The project exists to prove one idea: that you can add a **safety layer** on top of
ordinary time-optimal routing that penalizes the *inconvenience and danger of specific
unprotected maneuvers* while keeping routes efficient. Two maneuvers are committed:

1. **Unprotected lefts** onto busy streets (permissive/no control, not a protected arrow).
2. **Uncontrolled crossings** of busy streets (straight-through with no signal and not an
   all-way stop).

The safety model is **extensible in shape but closed in commitment**: the cost model
already generalizes (per-type severity plus control overrides), so new maneuver types
*could* be added — but we deliberately commit to exactly these two, validated, before
adding any others. This prevents both speculative generality and painting the model into
a corner.

Everything durable in the codebase — the cost model, the λ sweep, the detour budget, the
unsafe-action counters, the parity core — serves this thesis. It is the tiebreaker for
scope: a proposed feature either sharpens the safety tradeoff or it is drift.
