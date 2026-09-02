# "Busy road" is hybrid: a static floor plus a time-dynamic component

Whether a road is "busy" — and therefore dangerous to turn onto or cross unprotected — is
determined by a **hybrid** rule: a **static floor** from road character (OSM class, lanes,
tags) plus a **time-dynamic** component from simulated volume.

The thesis is fundamentally about road *character*: an unprotected left onto a four-lane
arterial is structurally dangerous regardless of the hour. So the static floor leads and
keeps the safety story intelligible and testable. The time-dynamic term adds realism
(danger rises at commute times) but is not allowed to let a busy road become "safe to
cross" at midnight — the floor holds.

## Status

Not yet implemented as decided. The current model in `config/config.toml [busy]` is
**pure time-of-day dynamic** ("busyness is time-of-day dependent by design") with a single
`busy_threshold`. A static floor from road character still needs to be added so a
low-volume hour cannot drop a major road below the threshold.
