"""A scenario reaching a real tester over the kernel ISO-TP path.

The unit tests drive a simulated clock and assert exact bytes at exact instants. These
cannot: the simulator is a separate process on a real bus, using real monotonic time. So
they assert the things only a real run can show -- that the value a tester reads changes
as seconds pass, that a timed trouble code arrives on an idle bus with nobody asking, that
clearing it over the wire makes it stay gone, and that the runtime still shuts down
cleanly with the tick running.

The profile is written here rather than reusing the shipped ice_scenario.yaml, whose
two-minute drive would make this suite two minutes long. Same features, seconds instead.
"""

from __future__ import annotations

import signal
import time

import pytest

from tests.integration.conftest import Simulator, open_tester_socket

PROFILE = """
version: 1
transport:
  interface: {interface}
vehicle:
  vin: TESTVIN0123456789
  type: ice
  speed: 0
  engine:
    rpm: 800
    coolant_temp: 20
    fuel_level: 50
scenario:
  tick: 0.05
  signals:
    - path: vehicle.speed
      type: timeline
      points:
        - {{at: 0.0, value: 0}}
        - {{at: 0.6, value: 40}}
        - {{at: 1.2, value: 90}}
ecus:
  engine:
    name: ECU_SIMULATOR
    dtcs:
      - code: P0128
        pending: false
        confirmed: false
    dtc_events:
      - {{at: 0.8, action: raise_confirmed, code: P0128}}
    dids: {{}}
    endpoints:
      - name: obd_physical
        rx: 0x7E0
        tx: 0x7E8
        addressing: physical
        protocols: [obd, uds]
        tx_padding: true
        pad_byte: 0x00
      - name: uds_physical
        rx: 0x7E1
        tx: 0x7E9
        addressing: physical
        protocols: [obd, uds]
        tx_padding: false
"""


@pytest.fixture
def scenario_simulator(vcan, tmp_path):
    path = tmp_path / "scenario.yaml"
    path.write_text(PROFILE.format(interface=vcan))
    sim = Simulator(vcan, str(tmp_path), profile=str(path))
    try:
        sim.wait_ready()
        yield sim
    finally:
        sim.terminate()


@pytest.fixture
def obd(vcan, scenario_simulator):
    sock = open_tester_socket(vcan, rx_id=0x7E8, tx_id=0x7E0)
    try:
        yield sock
    finally:
        sock.close()


def ask(sock, request):
    sock.send(request)
    return sock.recv()


def test_a_driven_signal_changes_between_two_reads(obd):
    # The whole point of the phase, on the wire: two identical requests, seconds apart,
    # answered with different bytes -- and not because the read had a side effect.
    first = ask(obd, b"\x01\x0d")
    time.sleep(1.4)
    later = ask(obd, b"\x01\x0d")
    assert first == b"\x41\x0d\x00"
    assert later == b"\x41\x0d\x5a", "90 km/h at the end of the timeline"


def test_two_reads_at_the_same_moment_agree(obd):
    time.sleep(1.4)  # past the end of the timeline, where the value is held
    assert len({ask(obd, b"\x01\x0d") for _ in range(10)}) == 1


def test_a_timed_trouble_code_arrives_with_nobody_asking(obd):
    # Nothing is sent on the bus for a second, so only the periodic tick can have applied
    # the event. Then one request finds it already there.
    assert ask(obd, b"\x03") == b"\x43\x00"
    time.sleep(1.2)
    assert ask(obd, b"\x03") == bytes.fromhex("43010128")


def test_the_same_code_is_visible_over_uds(vcan, scenario_simulator):
    time.sleep(1.2)
    uds = open_tester_socket(vcan, rx_id=0x7E9, tx_id=0x7E1)
    try:
        assert ask(uds, b"\x19\x02\xff") == bytes.fromhex("59028c" + "01280108")
    finally:
        uds.close()


def test_clearing_a_scenario_fault_over_the_wire_keeps_it_cleared(obd):
    time.sleep(1.2)
    assert ask(obd, b"\x03") == bytes.fromhex("43010128")
    assert ask(obd, b"\x04") == b"\x44"
    # The tick keeps running throughout; the event must not be applied a second time.
    for _ in range(6):
        time.sleep(0.2)
        assert ask(obd, b"\x03") == b"\x43\x00", "the scenario replayed a consumed event"


def test_tester_present_still_works_while_a_scenario_runs(vcan, scenario_simulator):
    uds = open_tester_socket(vcan, rx_id=0x7E9, tx_id=0x7E1)
    try:
        assert ask(uds, b"\x3e\x00") == b"\x7e\x00"
        uds.send(b"\x3e\x80")
        with pytest.raises(TimeoutError):
            uds.recv()
    finally:
        uds.close()


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["sigint", "sigterm"])
def test_shutdown_is_clean_while_the_tick_is_running(scenario_simulator, sig):
    # The Phase 2 lifecycle guarantee, with a periodic task in the picture: the tick is
    # cancelled and awaited on the way out, so the process still exits 0 within a second.
    time.sleep(0.3)  # let several ticks happen first
    started = time.monotonic()
    scenario_simulator.signal(sig)
    code, log = scenario_simulator.wait(timeout=5)
    assert code == 0
    assert time.monotonic() - started < 1.0
    assert "shutdown complete" in log
    assert "Task was destroyed" not in log and "Traceback" not in log
