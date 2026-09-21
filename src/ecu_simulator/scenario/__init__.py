"""Deterministic scenarios: how a simulated vehicle's state changes over time.

A scenario is pure configuration plus one writer. Generators are pure functions of
elapsed seconds; :class:`~ecu_simulator.scenario.runner.ScenarioRunner` is the only thing
that writes what they produce into domain state, and it writes only through the APIs that
already exist -- ``VehicleState.set``, ``DtcStore.update`` and ``DtcStore.clear``.

This package produces no bytes and imports no protocol module. It changes what the vehicle
*is*; the protocol layers go on encoding whatever they find, unchanged. Interfering with a
response -- dropping it, delaying it, forcing a negative one -- is fault injection, which
is a later phase and deliberately not a scenario feature.
"""

from pydantic import TypeAdapter

from ecu_simulator.scenario.generators import (
    ConstantSignal,
    RampSignal,
    SequenceSignal,
    SignalScenario,
    SineSignal,
    SteppedSignal,
    TimelineSignal,
)

__all__ = [
    "ConstantSignal",
    "RampSignal",
    "SequenceSignal",
    "SignalScenario",
    "SineSignal",
    "SteppedSignal",
    "TimelineSignal",
    "build_signal",
]

_SIGNAL_ADAPTER: TypeAdapter[SignalScenario] = TypeAdapter(SignalScenario)


def build_signal(data: object) -> SignalScenario:
    """Validate one scenario signal entry into the generator its ``type:`` names."""
    return _SIGNAL_ADAPTER.validate_python(data)
