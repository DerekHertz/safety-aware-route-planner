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

**Amended 2026-09-17: the floor is confirmed, and becomes an explicit named parameter.**

The floor holds -- a four-lane arterial does not become benign because it is empty -- but
it is set **low**, so a genuinely quiet street at 3am does stop being penalized. Exposure
really does fall at night; the reason the floor survives at all is that every *severity*
input moves the other way after midnight (speeds rise on empty arterials, sightlines are
worse, impaired driving peaks), and per vehicle-mile night driving is more fatal, not less.

The decisive argument for making this **explicit** is that today it is an accident. The
current model has no floor at all, so nighttime de-rating emerges from three weights
summing against one threshold. For a default primary arterial:

```
busy_score = 0.4*norm(speed) + 0.3*norm(lanes) + 0.3*norm(volume)
3am:   0.4(0.56) + 0.3(0.667) + 0.3(~0)  = 0.424  ->  NOT busy  (threshold 0.45)
peak:  0.424 + 0.3(0.75)                  = 0.649  ->  busy
```

A midnight arterial clears the "not busy" behaviour by **0.026**. Nudging `a_speed` from
0.40 to 0.43 would make every arterial permanently busy, with no code change and no test
failure. Whatever the behaviour should be, it must not live in the third decimal place of
a weight.

**To implement:** a `busy_floor_by_class` term in `config.toml [busy]`, giving each road
class a named minimum busy contribution that the volume term can move above but never
below, plus a scenario test pinning the 3am arterial case explicitly. Related: ADR-0010
records why the volume input stays synthetic for now, and why live speed cannot substitute
for it in this rule.
