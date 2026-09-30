"""DEV-26: a nonfinite scenario value validates, and later crashes the request it serves.

docs/known-deviations.md DEV-26. A profile whose scenario drives a signal with `.nan`,
`.inf` or `-.inf` passes profile validation today, even though decision 0002 says a
malformed profile is rejected at load with a path-qualified message
(docs/decisions/0002-configuration-format-and-validation.md:11-12). Applying the scenario
then raises an exception straight out of `Dispatcher.__call__` (`ecu/dispatcher.py:79`),
uncaught, for any request served while the bad value is live.

This file pins the whole chain, at unit level so it runs on hosted CI (no vcan0, no
can_isotp -- `FakeIsotpSocket` stands in for the kernel socket, exactly as
`tests/unit/test_isotp_transport.py` already does):

1. the profile validates through the real loader (YAML text -> `load_yaml` -> `parse_profile`);
2. serving a request while the value is live raises out of the dispatcher;
3. the transport's own request path (`transport/socketcan/transport.py:181-184`) catches
   that exception and sends no reply, exactly as it does for any other handler exception.

A strict xfail closes the file, asserting the corrected behavior decision 0002 promises:
rejection at load, with a path-qualified message. It is expected to keep failing until
DEV-26 is fixed; the marker is removed in the same commit that fixes it.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest

from ecu_simulator import app
from ecu_simulator.clock import SimulatedClock
from ecu_simulator.config import ConfigError, load_profile
from ecu_simulator.transport import DiagnosticRequest
from ecu_simulator.transport.socketcan import IsoTpTransport
from tests.characterization.conftest import xfail_deviation
from tests.unit.fakes import FakeIsotpSocket, factory

# A NaN in the second step of a repeating `stepped` signal on `vehicle.speed`: the
# generator named in the register's evidence (`scenario/generators.py:100`) driving the
# integral vehicle field whose `int()` cast is the one that raises
# (`scenario/runner.py:151`). Otherwise the smallest valid profile this schema accepts.
PROFILE = """\
version: 1

transport:
  interface: vcan0

vehicle:
  vin: TESTVIN0123456789
  type: ice
  speed: 0
  ambient_temp: 20
  battery_voltage: 14.1
  obd_standard: 1
  engine:
    rpm: 800
    coolant_temp: 20
    intake_temp: 20
    engine_load: 15.0
    throttle: 0.0
    maf: 2.0
    map: 33
    timing_advance: 10.0
    short_fuel_trim: 0.0
    long_fuel_trim: 1.5
    runtime: 0
    fuel_level: 62
    fuel_type: 1

scenario:
  tick: 0.5
  signals:
    - path: vehicle.speed
      type: stepped
      values: [0, .nan, 30]
      interval: 5

ecus:
  engine:
    name: ECU_SIMULATOR
    endpoints:
      - name: obd_physical
        rx: 0x7E0
        tx: 0x7E8
        addressing: physical
        protocols: [obd]
"""


def write_profile(tmp_path: Path) -> Path:
    path = tmp_path / "dev26_nonfinite.yaml"
    path.write_text(PROFILE)
    return path


def built_runtime(tmp_path: Path, clock: SimulatedClock) -> app.Runtime:
    config = app.RuntimeConfig.build(load_profile(write_profile(tmp_path)))
    return app.build_runtime(config, clock=clock)


# -- 1. the loader and the schema accept it ------------------------------------------------


def test_a_nonfinite_scenario_value_validates_through_the_real_loader(tmp_path):
    """DEV-26: `.nan` in a stepped `vehicle.speed` reaches `parse_profile` and is accepted."""
    profile = load_profile(write_profile(tmp_path))
    values = profile.scenario.signals[0].values
    assert values[0] == 0.0
    assert math.isnan(values[1])
    assert values[2] == 30.0


# -- 2. serving a request while the value is live raises out of the dispatcher -------------


def test_serving_a_request_while_the_value_is_live_raises_out_of_the_dispatcher(tmp_path):
    """DEV-26: at t=6 (inside the NaN step), `01 0D` raises from `Dispatcher.__call__`."""
    clock = SimulatedClock(0.0)
    runtime = built_runtime(tmp_path, clock)
    clock.advance(6.0)
    with pytest.raises(ValueError, match="cannot convert float NaN to integer"):
        runtime.dispatcher(DiagnosticRequest(bytes.fromhex("010D"), 0x7E0))


# -- 3. the transport catches it and sends no reply -----------------------------------------


@pytest.fixture(autouse=True)
def _clear_fake_instances():
    FakeIsotpSocket.instances.clear()
    yield
    for fake in FakeIsotpSocket.instances:
        if not fake.closed:
            fake.close()


def _fake_by_rx(rx_id: int) -> FakeIsotpSocket:
    for fake in FakeIsotpSocket.instances:
        for name, args in fake.calls:
            if name == "bind" and (args[1].get_rx_arbitration_id() & 0x1FFFFFFF) == rx_id:
                return fake
    raise AssertionError(f"no fake bound to rx 0x{rx_id:X}")


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_the_transport_catches_the_dispatcher_exception_and_sends_no_reply(tmp_path, caplog):
    """DEV-26: `IsoTpTransport._on_readable` catches it (transport/socketcan/transport.py:181-184).

    The tester's `01 0D` gets no reply -- dropped, not answered. The exception comes from
    `sync()`, which every request runs before routing (`ecu/dispatcher.py:79`), so *every*
    request is dropped while elapsed time sits in the NaN step, whichever PID it asks for
    -- proved here by a second, unrelated `01 00` at the same instant. The loop itself
    survives: once elapsed time leaves the NaN step (the `stepped` interval is 5s, so the
    next step starts at t=10), a further request is answered normally.
    """
    clock = SimulatedClock(0.0)
    runtime = built_runtime(tmp_path, clock)
    clock.advance(6.0)  # inside [5, 10): the NaN step
    endpoints = app.build_endpoints(runtime.config)
    transport = IsoTpTransport("vcan0", endpoints, socket_factory=factory(), check_environment=False)
    await transport.start(runtime.dispatcher)
    fake = _fake_by_rx(0x7E0)

    fake.feed.send(bytes.fromhex("010D"))  # vehicle speed: NaN at t=6, raises
    await _settle()
    fake.feed.send(bytes.fromhex("0100"))  # a different PID, same instant: raises too
    await _settle()

    assert fake.sent == []
    assert caplog.text.count("handler failed for request") == 2

    clock.advance(5.0)  # t=11, into [10, 15): the step is 30, not NaN
    fake.feed.send(bytes.fromhex("0100"))  # the loop is still alive and serves normally
    await _settle()
    await transport.stop()

    assert fake.sent == [b"\x41\x00\x1e\x3f\x80\x13"]


# -- corrected behavior (decision 0002), not yet implemented --------------------------------


@xfail_deviation("DEV-26", "a nonfinite scenario value is accepted at load instead of rejected")
def test_a_nonfinite_scenario_value_is_rejected_at_load(tmp_path):
    """The behavior decision 0002 promises: rejected at load, with a path-qualified message."""
    with pytest.raises(ConfigError, match=r"scenario\.signals\.0\.values\.1"):
        load_profile(write_profile(tmp_path))
