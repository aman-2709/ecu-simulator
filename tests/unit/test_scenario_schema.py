"""A scenario is rejected at load, with the path to the problem, or it is not loaded.

Everything a scenario could get wrong that would otherwise surface at the instant an
event fires -- a signal the configured vehicle does not have, a trouble code the ECU does
not carry, a generator parameter that makes no sense -- is a configuration error, reported
before a socket is opened, alongside every other problem in the file. That is the rule
Phase 4 established for the rest of the profile and nothing about a scenario earns an
exception to it.

Project input validation, not standards validation.
"""

import copy

import pytest

from ecu_simulator.config import ConfigError, parse_profile
from tests.unit.test_config_schema import VALID


def profile(scenario=None, dtc_events=None, vehicle=None):
    data = copy.deepcopy(VALID)
    if scenario is not None:
        data["scenario"] = scenario
    if dtc_events is not None:
        data["ecus"]["engine"]["dtc_events"] = dtc_events
    if vehicle is not None:
        data["vehicle"] = vehicle
    return parse_profile(data)


def refused(**kwargs):
    with pytest.raises(ConfigError) as error:
        profile(**kwargs)
    return str(error.value)


# --- a profile without a scenario ---------------------------------------------------------------


def test_a_profile_needs_no_scenario():
    parsed = profile()
    assert parsed.has_scenario is False
    assert parsed.scenario.signals == []


def test_the_shipped_profile_has_no_scenario():
    # Every golden byte and the V1.0 acceptance bench rest on the shipped profile's static
    # values. The demonstration of this feature is a separate profile.
    from ecu_simulator.cli import default_profile_path
    from ecu_simulator.config import load_profile

    assert load_profile(default_profile_path()).has_scenario is False


def test_a_profile_with_a_scenario_says_so():
    parsed = profile(scenario={"signals": [{"path": "vehicle.speed", "type": "constant", "value": 30}]})
    assert parsed.has_scenario is True


def test_dtc_events_alone_are_a_scenario():
    parsed = profile(dtc_events=[{"at": 10, "action": "raise_confirmed", "code": "P0001"}])
    assert parsed.has_scenario is True


# --- signals -------------------------------------------------------------------------------------


def test_a_signal_the_configured_vehicle_does_not_have_is_refused_with_its_path():
    message = refused(scenario={"signals": [{"path": "battery.soc", "type": "constant", "value": 50}]})
    assert "battery.soc" in message
    assert "scenario.signals.0.path" in message


def test_a_signal_that_is_not_a_dotted_path_is_refused():
    assert "nonsense" in refused(scenario={"signals": [{"path": "nonsense", "type": "constant", "value": 1}]})


def test_a_signal_that_is_not_a_number_is_refused():
    # vehicle.vin exists, but a generator produces numbers and the VIN is text.
    message = refused(scenario={"signals": [{"path": "vehicle.vin", "type": "constant", "value": 1}]})
    assert "vehicle.vin" in message


def test_two_signals_driving_one_path_are_refused():
    message = refused(
        scenario={
            "signals": [
                {"path": "vehicle.speed", "type": "constant", "value": 1},
                {"path": "vehicle.speed", "type": "constant", "value": 2},
            ]
        }
    )
    assert "vehicle.speed" in message


def test_an_unknown_generator_type_is_refused_by_name():
    message = refused(scenario={"signals": [{"path": "vehicle.speed", "type": "sawtooth"}]})
    assert "scenario.signals.0" in message


def test_a_signal_without_a_type_is_refused():
    assert "type" in refused(scenario={"signals": [{"path": "vehicle.speed", "value": 1}]})


@pytest.mark.parametrize(
    "signal",
    [
        {"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 10, "over": 0},
        {"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 10, "over": -5},
        {"path": "engine.rpm", "type": "sine", "centre": 800, "amplitude": 100, "period": 0},
        {"path": "vehicle.speed", "type": "stepped", "values": [], "interval": 5},
        {"path": "vehicle.speed", "type": "stepped", "values": [1], "interval": 0},
        {"path": "vehicle.speed", "type": "sequence", "steps": []},
        {"path": "vehicle.speed", "type": "sequence", "steps": [{"value": 1, "for": 0}]},
        {"path": "vehicle.speed", "type": "timeline", "points": []},
        {"path": "vehicle.speed", "type": "timeline", "points": [{"at": 5, "value": 1}, {"at": 1, "value": 2}]},
    ],
    ids=[
        "ramp-zero-duration",
        "ramp-negative-duration",
        "sine-zero-period",
        "stepped-no-values",
        "stepped-zero-interval",
        "sequence-no-steps",
        "sequence-zero-duration",
        "timeline-no-points",
        "timeline-out-of-order",
    ],
)
def test_a_generator_parameter_that_makes_no_sense_is_refused(signal):
    assert refused(scenario={"signals": [signal]})


def test_an_unknown_key_in_a_generator_is_refused():
    assert refused(scenario={"signals": [{"path": "vehicle.speed", "type": "constant", "value": 1, "unit": "kph"}]})


def test_every_problem_in_a_scenario_is_reported_at_once():
    # Phase 4's rule: a profile is fixed once, not one error per run.
    message = refused(
        scenario={
            "signals": [
                {"path": "battery.soc", "type": "constant", "value": 1},
                {"path": "vehicle.vin", "type": "constant", "value": 2},
            ]
        }
    )
    assert "scenario.signals.0.path" in message
    assert "scenario.signals.1.path" in message


def test_two_bad_events_are_reported_at_once():
    message = refused(
        dtc_events=[
            {"at": 1, "action": "raise_pending", "code": "P0099"},
            {"at": 2, "action": "raise_pending", "code": "P0098"},
        ]
    )
    assert "ecus.engine.dtc_events.0.code" in message
    assert "ecus.engine.dtc_events.1.code" in message


# --- the tick ------------------------------------------------------------------------------------


def test_the_tick_has_a_documented_default():
    assert profile().scenario.tick == 1.0


def test_the_tick_can_be_set():
    assert profile(scenario={"tick": 0.25, "signals": []}).scenario.tick == 0.25


@pytest.mark.parametrize("tick", [0, -1])
def test_a_tick_that_is_not_positive_is_refused(tick):
    assert "tick" in refused(scenario={"tick": tick})


# --- DTC events ------------------------------------------------------------------------------------


def test_a_dtc_event_is_accepted_beside_the_codes_it_acts_on():
    parsed = profile(dtc_events=[{"at": 30, "action": "raise_confirmed", "code": "P0001"}])
    events = parsed.ecus["engine"].dtc_events
    assert [(e.at, e.action, e.code) for e in events] == [(30, "raise_confirmed", "P0001")]


def test_an_event_naming_a_code_this_ecu_does_not_carry_is_refused():
    # DtcStore.update raises KeyError for an unconfigured code by design, so a scenario
    # naming one is refused at load rather than at the instant it would have fired.
    message = refused(dtc_events=[{"at": 1, "action": "raise_pending", "code": "P0099"}])
    assert "P0099" in message
    assert "ecus.engine.dtc_events.0.code" in message


def test_an_event_naming_a_code_that_cannot_exist_is_refused():
    assert refused(dtc_events=[{"at": 1, "action": "raise_pending", "code": "not-a-code"}])


def test_an_unknown_action_is_refused():
    assert "action" in refused(dtc_events=[{"at": 1, "action": "set_fire_to", "code": "P0001"}])


def test_an_action_that_needs_a_code_is_refused_without_one():
    assert refused(dtc_events=[{"at": 1, "action": "raise_pending"}])


def test_clear_all_takes_no_code():
    assert refused(dtc_events=[{"at": 1, "action": "clear_all", "code": "P0001"}])
    assert profile(dtc_events=[{"at": 1, "action": "clear_all"}]).ecus["engine"].dtc_events[0].code is None


def test_an_event_before_time_zero_is_refused():
    assert refused(dtc_events=[{"at": -1, "action": "raise_pending", "code": "P0001"}])


def test_events_may_share_a_time():
    parsed = profile(
        dtc_events=[
            {"at": 5, "action": "raise_pending", "code": "P0001"},
            {"at": 5, "action": "raise_confirmed", "code": "B1477"},
        ]
    )
    assert len(parsed.ecus["engine"].dtc_events) == 2


def test_events_need_not_be_in_ascending_order():
    # Unlike a timeline, which is read by interpolation, events are applied by marker, so
    # the order in the file is only the tie-break for events sharing a time.
    parsed = profile(
        dtc_events=[
            {"at": 9, "action": "raise_pending", "code": "P0001"},
            {"at": 2, "action": "raise_confirmed", "code": "B1477"},
        ]
    )
    assert [e.at for e in parsed.ecus["engine"].dtc_events] == [9, 2]


# --- what a scenario may not contain --------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    [
        {"signals": [], "when": {"speed": "> 50"}},
        {"signals": [], "faults": [{"drop": "0x7E0"}]},
        {"signals": [], "seed": 42},
        {"signals": [], "triggers": [{"on": "0322"}]},
    ],
    ids=["conditions", "fault-injection", "randomness", "triggers"],
)
def test_a_scenario_cannot_smuggle_in_a_later_phases_feature(scenario):
    # Fault injection is Phase 10 and conditions, branching and triggers are in no phase.
    # `extra="forbid"` is what keeps a scenario a pure function of time, and this test is
    # what keeps that from being relaxed by accident.
    assert refused(scenario=scenario)
