"""The one writer: turn generator values and timed events into domain state.

``ScenarioRunner.apply(t)`` is the only thing in this project that writes what a scenario
produces, and it writes only through ``VehicleState.set``, ``DtcStore.update`` and
``DtcStore.clear`` -- the domain APIs that already existed. It produces no bytes and
imports no protocol module.

Two invariants hold it together.

**Idempotence for a fixed t.** ``apply(t)`` twice at the same ``t`` leaves identical
state. Signals get this for free: a generator is a pure function of ``t``, so writing its
value again writes the same number. Timed events do not, because raising a code and then
raising it again after a tester has cleared it is a visible difference on the wire. Each
event therefore carries an applied-marker and is consumed the first time it is due. That
makes double application impossible by construction rather than by arithmetic on a
previous timestamp, and it is what the periodic tick and the request path share: whichever
reaches an event first consumes it, and the other finds nothing to do.

**Nothing here advances time.** The clock reports it and the runner reads it. Handling a
diagnostic request synchronises domain state to the time that has already passed; it never
derives state from how many requests have arrived, and asking twice at one instant is
indistinguishable from asking once.

``apply`` is synchronous and performs no awaits, so on the single asyncio event loop it
runs to completion without yielding. The periodic tick and the request path can interleave
between calls but never inside one, which is why the shared cursor needs no lock and why
no thread is started. Tests assert that invariant rather than trusting the comment; if
``apply`` ever has to await something, the cursor needs one serialization mechanism and
those tests are where that starts.

See docs/decisions/0006-phase-7-scenario-and-testerpresent.md sections 5 and 6.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from ecu_simulator.dtc import DtcStore
from ecu_simulator.scenario.events import CODE_ACTIONS, DtcEvent
from ecu_simulator.scenario.generators import SignalScenario
from ecu_simulator.vehicle import VehicleState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Scenario:
    """Pure configuration: what changes, and when. No state, no clock.

    ``events`` pairs each event with the name of the ECU whose store it acts on, in the
    order the profile lists them; events sharing an ``at`` are applied in that order.
    """

    signals: tuple[SignalScenario, ...] = ()
    events: tuple[tuple[str, DtcEvent], ...] = ()


@dataclass(slots=True)
class _Event:
    """One configured event plus the marker that stops it being applied twice."""

    ecu: str
    event: DtcEvent
    applied: bool = field(default=False)


class ScenarioRunner:
    def __init__(
        self,
        scenario: Scenario,
        vehicle: VehicleState,
        dtc_stores: Mapping[str, DtcStore],
    ) -> None:
        """Validate the scenario against the vehicle and the stores it will write to.

        Everything that could fail at the instant an event fires is checked here instead:
        a signal path the configured vehicle does not have, two generators driving one
        path, an ECU with no store, a trouble code that ECU does not carry. Profile
        validation catches all of these earlier still, with the path to the problem; this
        is the backstop for a runner built any other way, and it means ``apply`` has no
        failure mode of its own.
        """
        self._scenario = scenario
        self._vehicle = vehicle
        self._stores = dict(dtc_stores)
        seen: set[str] = set()
        for signal in scenario.signals:
            if signal.path in seen:
                raise ValueError(f"two scenario signals drive {signal.path!r}; a signal has one generator")
            seen.add(signal.path)
            if not vehicle.has(signal.path):
                raise KeyError(f"scenario signal {signal.path!r} is not a signal this vehicle has")
        self._events = [_Event(ecu, event) for ecu, event in scenario.events]
        for entry in self._events:
            store = self._stores.get(entry.ecu)
            if store is None:
                raise KeyError(f"scenario event names ECU {entry.ecu!r}, which has no trouble-code store")
            if entry.event.code is not None and entry.event.code not in store:
                raise KeyError(
                    f"scenario event names trouble code {entry.event.code!r}, "
                    f"which is not configured on ECU {entry.ecu!r}"
                )
        # The type each signal's value is put back as. Read once, from the vehicle, so a
        # generator's float does not leave a float where the encoders expect whole units.
        self._integral = {
            signal.path: isinstance(vehicle.get(signal.path), int) and not isinstance(vehicle.get(signal.path), bool)
            for signal in scenario.signals
        }
        self._last_applied: float | None = None

    def __repr__(self) -> str:
        return f"ScenarioRunner(signals={len(self._scenario.signals)}, events={len(self._events)})"

    @property
    def last_applied(self) -> float | None:
        """The highest ``t`` applied so far, or ``None`` if the runner has not been used."""
        return self._last_applied

    @property
    def pending_events(self) -> int:
        """How many timed events have not been consumed yet."""
        return sum(1 for entry in self._events if not entry.applied)

    def apply(self, t: float) -> None:
        """Bring domain state up to elapsed time ``t``.

        Deterministic and idempotent for a fixed ``t``. A ``t`` earlier than one already
        applied is refused with a warning rather than reinterpreted: the production clock
        is monotonic and ``SimulatedClock`` cannot be advanced backwards, so a backward
        ``t`` means something is wrong, and rewind or replay semantics are not something
        this phase implements. Refusing is also the safe answer on the request path, where
        raising would turn a clock anomaly into a dropped diagnostic response.
        """
        if self._last_applied is not None and t < self._last_applied:
            logger.warning(
                "scenario time went backwards (%.6f after %.6f); the request is served from "
                "the state already reached and no event is released",
                t,
                self._last_applied,
            )
            return
        self._apply_signals(t)
        self._apply_events(t)
        self._last_applied = t

    def _apply_signals(self, t: float) -> None:
        for signal in self._scenario.signals:
            value = signal.value_at(t)
            self._vehicle.set(signal.path, int(value) if self._integral[signal.path] else value)

    def _apply_events(self, t: float) -> None:
        for entry in self._events:
            if entry.applied or entry.event.at > t:
                continue
            self._apply_event(entry)
            entry.applied = True

    def _apply_event(self, entry: _Event) -> None:
        store = self._stores[entry.ecu]
        event = entry.event
        logger.info(
            "scenario: %s on %s at t=%s%s",
            event.action,
            entry.ecu,
            event.at,
            f" ({event.code})" if event.code else "",
        )
        if event.action == "clear_all":
            store.clear()
            return
        assert event.code is not None  # the model requires one for every other action
        store.update(event.code, **CODE_ACTIONS[event.action])
