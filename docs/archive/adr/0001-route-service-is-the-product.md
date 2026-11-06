# The route service is the product; the route artifact is the contract boundary

The deliverable is the **route service** — the routing engine plus its API — and the
**route artifact** it emits is the versioned, contract-tested boundary. Everything else
is a *consumer* of that artifact: `web/` is a reference client, and live navigation is a
separate downstream consumer. The engine knows nothing about GPS, screens, or clients.

We chose this because the sophistication here is the engine (edge-based turn graph,
Python↔C++ parity, deterministic packs), and it should be reusable by any client without
that client leaking back into it. `web/lib/units.ts` already anticipated this ("the API
always speaks SI so the response contract stays framework-agnostic for a future mobile
client"). The alternative — letting clients reach into engine internals or grow
client-specific endpoints — is what let the live-nav work destabilize the planner (see
ADR-0008).

## Consequences

- The route artifact schema is treated as sacred: versioned and contract-tested (see
  ADR-0004). Breaking it is a deliberate, visible act.
- New client features are not allowed to add engine responsibilities. If a client needs
  something, it either derives it from the artifact or the artifact grows by contract.
