"""The shipped demonstration profile, driven by a simulated clock.

ice_default.yaml has no scenario and must not gain one. This second profile is where the
feature is demonstrated, and it is exercised here at known instants rather than by waiting
for real seconds to pass, so the test is a few milliseconds long and says the same thing
every time it runs.
"""

from pathlib import Path

import pytest

from ecu_simulator import app, cli
from ecu_simulator.clock import SimulatedClock
from ecu_simulator.config import load_profile
from ecu_simulator.transport import DiagnosticRequest

SCENARIO_PROFILE = Path(cli.__file__).resolve().parent / "profiles" / "ice_scenario.yaml"


def built(clock):
    return app.build_runtime(app.RuntimeConfig.build(load_profile(SCENARIO_PROFILE)), clock=clock)


def ask(runtime, hex_request, address=0x7E0):
    response = runtime.dispatcher(DiagnosticRequest(bytes.fromhex(hex_request), address))
    return response.payload.hex() if response is not None else None


def test_the_demonstration_profile_is_shipped_beside_the_default():
    assert SCENARIO_PROFILE.is_file()
    assert cli.default_profile_path().name == "ice_default.yaml"


def test_it_is_a_valid_profile_and_it_has_a_scenario():
    profile = load_profile(SCENARIO_PROFILE)
    assert profile.has_scenario is True
    assert {signal.path for signal in profile.scenario.signals} == {
        "vehicle.speed",
        "engine.rpm",
        "engine.coolant_temp",
        "engine.engine_load",
        "engine.throttle",
        "engine.fuel_level",
    }


def test_it_uses_every_generator_the_plan_names():
    profile = load_profile(SCENARIO_PROFILE)
    kinds = {signal.type for signal in profile.scenario.signals}
    assert {"timeline", "ramp", "sine", "stepped"} <= kinds


def test_it_serves_the_same_addresses_as_the_default_profile():
    default = load_profile(cli.default_profile_path())
    scenario = load_profile(SCENARIO_PROFILE)
    assert [(e.rx, e.tx) for e in default.ecus["engine"].endpoints] == [
        (e.rx, e.tx) for e in scenario.ecus["engine"].endpoints
    ]


# --- the drive, at known instants ------------------------------------------------------------


@pytest.mark.parametrize(
    "at, speed",
    [(0, "00"), (10, "00"), (20, "1e"), (45, "50"), (70, "50"), (90, "00"), (115, "46"), (120, "00")],
)
def test_road_speed_follows_the_timeline(at, speed):
    clock = SimulatedClock()
    runtime = built(clock)
    clock.advance(at)
    assert ask(runtime, "010d") == "410d" + speed


def test_the_engine_warms_up_over_the_first_minute():
    clock = SimulatedClock()
    runtime = built(clock)
    assert ask(runtime, "0105") == "41053c", "20 C at the start"
    clock.advance(30)
    assert ask(runtime, "0105") == "410560", "56 C halfway up the ramp"
    clock.advance(30)
    assert ask(runtime, "0105") == "410584", "92 C, and held"
    clock.advance(600)
    assert ask(runtime, "0105") == "410584"


def test_the_throttle_staircase_repeats():
    clock = SimulatedClock()
    runtime = built(clock)
    first = [ask(runtime, "0111") for _ in range(1)]
    clock.advance(48)  # four steps of twelve seconds
    assert [ask(runtime, "0111")] == first


def test_several_parameters_come_back_in_one_answer_and_all_of_them_move():
    clock = SimulatedClock()
    runtime = built(clock)
    at_rest = ask(runtime, "010d0c05")
    clock.advance(45)
    moving = ask(runtime, "010d0c05")
    assert at_rest != moving
    assert moving.startswith("410d50") and moving.endswith("05" + "72"), "80 km/h, 74 C"


# --- the fault appears, and stays gone once cleared ---------------------------------------------


def test_no_trouble_code_is_raised_at_the_start():
    runtime = built(SimulatedClock())
    assert ask(runtime, "03") == "4300"
    assert ask(runtime, "1902ff", 0x7E1) == "59028c"


def test_the_thermostat_code_goes_pending_then_confirmed_with_the_indicator():
    clock = SimulatedClock()
    runtime = built(clock)
    clock.advance(40)
    assert ask(runtime, "03") == "4300", "pending is not what service 03 reports"
    assert ask(runtime, "1902ff", 0x7E1) == "59028c" + "01280104"
    clock.advance(35)
    assert ask(runtime, "03") == "43010128"
    assert ask(runtime, "1902ff", 0x7E1) == "59028c" + "0128018c"


def test_clearing_the_fault_keeps_it_cleared():
    clock = SimulatedClock()
    runtime = built(clock)
    clock.advance(80)
    assert ask(runtime, "03") == "43010128"
    assert ask(runtime, "04") == "44"
    for seconds in (0, 1, 40, 3600):
        clock.advance(seconds)
        assert ask(runtime, "03") == "4300", f"the fault came back {seconds}s later"


def test_the_same_advance_sequence_produces_the_same_run_twice():
    def drive():
        clock = SimulatedClock()
        runtime = built(clock)
        seen = []
        for _ in range(40):
            seen.append(ask(runtime, "010d0c05112f"))
            seen.append(ask(runtime, "03"))
            clock.advance(3.25)
        return seen

    assert drive() == drive()


def test_restarting_replays_the_scenario_from_the_beginning():
    first, second = built(SimulatedClock()), built(SimulatedClock())
    assert ask(first, "010d0c05") == ask(second, "010d0c05")
    assert ask(first, "03") == ask(second, "03") == "4300"
