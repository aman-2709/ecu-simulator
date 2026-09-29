"""The stepped 90 s drive-cycle demo profile (design: docs/plans/moving-vehicle-demo-design.md §4).

This profile is not package data. It lives under `docs/examples/` and is run with
`--profile docs/examples/ice_drive_cycle_stepped.yaml`. It uses only today's generators
(`stepped` and `ramp`), so it needs no scenario or engine code change: the smallest path
to a moving vehicle before the timeline extension (§5) lands. It is exercised here at
known instants rather than by waiting for real seconds to pass.
"""

from pathlib import Path

import pytest

from ecu_simulator import app
from ecu_simulator.clock import SimulatedClock
from ecu_simulator.config import load_profile
from ecu_simulator.transport import DiagnosticRequest

REPO = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPO / "docs" / "examples" / "ice_drive_cycle_stepped.yaml"

KEY_PIDS = "010c0d110405"


def built(clock):
    return app.build_runtime(app.RuntimeConfig.build(load_profile(PROFILE_PATH)), clock=clock)


def ask(runtime, hex_request, address=0x7E0):
    response = runtime.dispatcher(DiagnosticRequest(bytes.fromhex(hex_request), address))
    return response.payload.hex(" ").upper() if response is not None else None


def test_the_profile_file_exists():
    assert PROFILE_PATH.is_file()


def test_it_is_a_valid_profile_loaded_through_the_real_loader():
    profile = load_profile(PROFILE_PATH)
    assert profile.has_scenario is True


def test_every_stepped_signal_has_ninety_values_and_interval_one():
    profile = load_profile(PROFILE_PATH)
    stepped_signals = [s for s in profile.scenario.signals if s.type == "stepped"]
    assert {s.path for s in stepped_signals} == {
        "vehicle.speed",
        "engine.rpm",
        "engine.throttle",
        "engine.engine_load",
    }
    for signal in stepped_signals:
        assert len(signal.values) == 90, signal.path
        assert signal.interval == 1, signal.path


def test_coolant_is_the_non_repeating_ramp():
    profile = load_profile(PROFILE_PATH)
    coolant = next(s for s in profile.scenario.signals if s.path == "engine.coolant_temp")
    assert coolant.type == "ramp"


def test_there_is_no_odometer_signal():
    profile = load_profile(PROFILE_PATH)
    assert not any("odometer" in s.path for s in profile.scenario.signals)


# --- key OBD replies at the design's representative whole-second times, verbatim -------------


@pytest.mark.parametrize(
    "label, at, expected",
    [
        ("idle", 2.0, "41 0C 0C 80 0D 00 11 00 04 33 05 3C"),
        ("mid-acceleration, 2nd gear", 12.0, "41 0C 1E A0 0D 23 11 72 04 BF 05 3F"),
        ("same step, 0.5 s later", 12.5, "41 0C 1E A0 0D 23 11 72 04 BF 05 3F"),
        ("upshift begins", 15.0, "41 0C 2B C0 0D 32 11 72 04 BF 05 40"),
        ("t = 15.3", 15.3, "41 0C 2B C0 0D 32 11 72 04 BF 05 40"),
        ("upshift ends, top gear", 16.0, "41 0C 19 C8 0D 37 11 72 04 BF 05 40"),
        ("cruise", 40.0, "41 0C 25 80 0D 50 11 2D 04 59 05 47"),
        ("brake, first step", 61.0, "41 0C 23 00 0D 4A 11 00 04 19 05 4D"),
        ("mid-brake", 65.0, "41 0C 19 00 0D 35 11 00 04 19 05 4E"),
        ("2nd cycle, mid-acceleration", 102.0, "41 0C 1E A0 0D 23 11 72 04 BF 05 59"),
        ("2nd cycle, t = 105.3", 105.3, "41 0C 2B C0 0D 32 11 72 04 BF 05 5A"),
        ("2nd cycle, cruise", 130.0, "41 0C 25 80 0D 50 11 2D 04 59 05 61"),
        ("warm, 4th cycle, cruise", 310.0, "41 0C 25 80 0D 50 11 2D 04 59 05 82"),
    ],
)
def test_key_obd_replies_match_the_design_exactly(label, at, expected):
    clock = SimulatedClock()
    runtime = built(clock)
    clock.advance(at)
    assert ask(runtime, KEY_PIDS) == expected, label


# --- the loop boundary, just before, at and just after 90 s and 180 s ------------------------


@pytest.mark.parametrize(
    "label, at",
    [
        ("just before the boundary", 89.999),
        ("one ulp before the boundary", 89.99999999999999),
        ("exactly at the boundary", 90.0),
        ("just after the boundary", 90.001),
    ],
)
def test_the_first_loop_boundary_gives_idle_bytes(label, at):
    clock = SimulatedClock()
    runtime = built(clock)
    clock.advance(at)
    assert ask(runtime, KEY_PIDS) == "41 0C 0C 80 0D 00 11 00 04 33 05 56", label


def test_the_second_loop_boundary_gives_idle_bytes():
    clock = SimulatedClock()
    runtime = built(clock)
    clock.advance(180.0)
    assert ask(runtime, KEY_PIDS) == "41 0C 0C 80 0D 00 11 00 04 33 05 70"


# --- the coolant ramp keeps rising across loop boundaries -------------------------------------


@pytest.mark.parametrize("t", [12.0, 102.0])
def test_coolant_in_the_second_cycle_is_higher_than_the_first(t):
    clock = SimulatedClock()
    runtime = built(clock)
    clock.advance(t)
    first_cycle = ask(runtime, KEY_PIDS)

    clock2 = SimulatedClock()
    runtime2 = built(clock2)
    clock2.advance(t + 90)
    second_cycle = ask(runtime2, KEY_PIDS)

    first_bytes = first_cycle.split(" ")
    second_bytes = second_cycle.split(" ")
    # the coolant byte is the last byte of the reply; every other stored value repeats.
    assert first_bytes[:-1] == second_bytes[:-1]
    assert int(second_bytes[-1], 16) > int(first_bytes[-1], 16)
