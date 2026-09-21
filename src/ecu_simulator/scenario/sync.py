"""Scenario time: clock readings turned into elapsed seconds and handed to the runner.

The one place that knows a clock exists. It is deliberately not the runner, which is told
what ``t`` is and can therefore be reproduced entirely from a ``SimulatedClock`` and an
advance sequence, and deliberately not the ``Dispatcher``, which is handed a callable and
never learns that scenarios exist at all.

The origin is captured once, when the runtime starts, so scenario time begins at zero
whatever the monotonic clock happens to read and a restart replays the scenario from the
configured initial state.
"""

from __future__ import annotations

from ecu_simulator.clock import Clock
from ecu_simulator.scenario.runner import ScenarioRunner


class ScenarioSync:
    """Bring domain state up to the time that has already passed. Advances nothing."""

    def __init__(self, runner: ScenarioRunner, clock: Clock, origin: float | None = None) -> None:
        self._runner = runner
        self._clock = clock
        self._origin = clock.now() if origin is None else origin

    def __repr__(self) -> str:
        return f"ScenarioSync(origin={self._origin!r})"

    @property
    def elapsed(self) -> float:
        return self._clock.now() - self._origin

    def __call__(self) -> None:
        self._runner.apply(self.elapsed)
