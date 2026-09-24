"""Departure time in the served pack's local clock (ADR-0014 decision 7, #63).

`sim.profiles.multipliers_at` reads only a departure's `.hour/.minute/.second`,
so the datetime handed to the router IS the traffic hour. It must therefore be
the pack's local wall clock, never the server's (UTC in the container).

Two pure pieces, kept apart from `AppState` and the route handlers so the
per-pack registry (ADR-0014 step 1) can call them per pack unchanged:

- `pack_timezone`: which zone a pack lives in — an IANA `timezone` on its
  `[region.presets.<name>]` in config (config, not the manifest, so published
  packs need no rebuild);
- `resolve_departure`: a request's departure → a naive pack-local datetime.
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pyref.config import Config

UTC_ZONE = ZoneInfo("UTC")


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def resolve_departure(departure: datetime.datetime | None, tz: ZoneInfo,
                      now: Callable[[], datetime.datetime] | None = None,
                      ) -> datetime.datetime:
    """The naive pack-local wall-clock time a route is priced at.

    - **naive** → returned unchanged. A naive departure is taken to be
      pack-local already: that is what the web client sends (browser-local
      time for a map of the metro it is looking at) and what `/route` has
      always done with it. Documented here rather than guessed at.
    - **aware** → an instant: converted into `tz`, then the offset dropped.
    - **None** → "now" in `tz`, offset dropped. `now` returns an aware
      instant (default: the real UTC clock) and is injectable for tests.

    The result is always naive, so the cost model, the golden digests and the
    C++ parity core see exactly the kind of value they always have.
    """
    if departure is None:
        departure = (now or _utc_now)()
    if departure.tzinfo is None or departure.utcoffset() is None:
        return departure
    return departure.astimezone(tz).replace(tzinfo=None)


def pack_timezone(cfg: Config, region: str | None, *,
                  allow_unconfigured: bool) -> ZoneInfo:
    """The IANA zone of the pack whose manifest names `region`.

    A served pack whose region is a config preset MUST carry a valid
    `timezone`, or this raises — called at startup, so a misconfigured
    deployment crash-loops instead of pricing traffic at the wrong hour.

    A region that is not a preset at all (the API tests' toy pack, manifest
    `region: "toy"`, or a null region) only exists when `SR_PACK_DIR` pins a
    pack directory by hand; `allow_unconfigured` says so, and such a pack is
    served in UTC — the zone that claims no local time. Without it, an
    unconfigured pack is fatal too.
    """
    presets = cfg["region"]["presets"]
    if region is None or region not in presets:
        if allow_unconfigured:
            return UTC_ZONE
        raise ValueError(f"served pack region {region!r} is not a "
                         f"[region.presets] entry, so it has no timezone")
    name = presets[region].get("timezone")
    if not name:
        raise ValueError(f"[region.presets.{region}] has no timezone; the served "
                         f"pack needs an IANA zone to resolve departure times")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"[region.presets.{region}] timezone {name!r} is not "
                         f"a known IANA zone") from exc
