"""Time source.

Production code takes the time from a :class:`Clock` rather than calling the clock
functions directly, so tests can drive time instead of sleeping. Phase 7 builds the
deterministic scenario generators on this seam; Phase 4 only establishes it.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float:
        """Seconds since an arbitrary fixed origin; monotonic, never decreasing."""
        ...


class MonotonicClock:
    """Real time, immune to wall-clock adjustments."""

    def now(self) -> float:
        return time.monotonic()


class SimulatedClock:
    """Time that moves only when a test advances it."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError(f"cannot advance a clock by a negative amount ({seconds})")
        self._now += float(seconds)
        return self._now
