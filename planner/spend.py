"""The daily spend cap (ADR-0019 "Privacy and cost"): past it, `/v1/plan` is 503.

In-process and per UTC day. That is enough for one host and a handful of
tester tokens; a restart forgets the day's spend, which can only let a capped
day spend up to twice its cap. Move it into the shared store before the
planner runs on more than one replica.
"""
from __future__ import annotations

import datetime
import os
import threading
from collections.abc import Callable

DEFAULT_DAILY_USD = 1.0


def _utc_today() -> datetime.date:
    return datetime.datetime.now(datetime.UTC).date()


class SpendMeter:
    def __init__(self, daily_usd: float | None = None,
                 today: Callable[[], datetime.date] = _utc_today) -> None:
        if daily_usd is None:
            daily_usd = float(os.environ.get("SR_PLANNER_DAILY_USD", DEFAULT_DAILY_USD))
        self.daily_usd = daily_usd
        self._today = today
        self._day = today()
        self._spent = 0.0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._spent = day, 0.0

    def exhausted(self) -> bool:
        with self._lock:
            self._roll()
            return self._spent >= self.daily_usd

    def add(self, usd: float) -> None:
        with self._lock:
            self._roll()
            self._spent += usd

    @property
    def spent_today(self) -> float:
        with self._lock:
            self._roll()
            return self._spent
