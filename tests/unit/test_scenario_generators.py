"""The six deterministic generators, as pure functions of elapsed seconds.

Every generator is a value at a time and nothing else: it holds no state, reads no clock,
and knows nothing about what the signal it drives means. `value_at(t)` called twice with
the same `t` returns the same number, which is what makes a whole scenario reproducible
from a `SimulatedClock` and an advance sequence.

There is no randomness here, seeded or otherwise. Phase 7 needs none, so there is nothing
to seed; a future phase that wants noise brings its own seed with it.

The plan names exactly these six. See docs/decisions/0006, section 5.1.
"""

import math

import pytest
from pydantic import ValidationError

from ecu_simulator.scenario import build_signal
from ecu_simulator.scenario.generators import (
    ConstantSignal,
    RampSignal,
    SequenceSignal,
    SineSignal,
    SteppedSignal,
    TimelineSignal,
)

EVERY_GENERATOR = [
    {"path": "vehicle.speed", "type": "constant", "value": 42},
    {"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 120, "over": 60},
    {"path": "vehicle.speed", "type": "sine", "centre": 50, "amplitude": 10, "period": 20},
    {"path": "vehicle.speed", "type": "stepped", "values": [10, 20, 30], "interval": 5},
    {"path": "vehicle.speed", "type": "sequence", "steps": [{"value": 10, "for": 5}, {"value": 20, "for": 5}]},
    {"path": "vehicle.speed", "type": "timeline", "points": [{"at": 0, "value": 0}, {"at": 10, "value": 99}]},
]


# --- the shape of the table ---------------------------------------------------------------


def test_the_plan_names_exactly_six_generators():
    kinds = {build_signal(entry).type for entry in EVERY_GENERATOR}
    assert kinds == {"constant", "ramp", "sine", "stepped", "sequence", "timeline"}


@pytest.mark.parametrize("entry", EVERY_GENERATOR, ids=lambda e: str(e["type"]))
def test_every_generator_is_a_pure_function_of_t(entry):
    signal = build_signal(entry)
    for t in (0.0, 0.5, 3.0, 7.25, 60.0, 1000.0):
        assert signal.value_at(t) == signal.value_at(t)


@pytest.mark.parametrize("entry", EVERY_GENERATOR, ids=lambda e: str(e["type"]))
def test_two_generators_built_from_the_same_configuration_agree(entry):
    # Determinism across instances, not just across calls: nothing is carried in an
    # object's identity or in the order it was built.
    first, second = build_signal(entry), build_signal(entry)
    assert [first.value_at(t) for t in range(0, 100)] == [second.value_at(t) for t in range(0, 100)]


@pytest.mark.parametrize("entry", EVERY_GENERATOR, ids=lambda e: str(e["type"]))
def test_no_generator_holds_state_that_a_call_changes(entry):
    signal = build_signal(entry)
    before = signal.model_dump()
    signal.value_at(17.0)
    assert signal.model_dump() == before


def test_an_unknown_generator_type_is_refused():
    with pytest.raises(ValidationError):
        build_signal({"path": "vehicle.speed", "type": "sawtooth", "value": 1})


def test_a_generator_without_a_type_is_refused():
    # Pydantic 2.13 forbids a before/wrap/plain validator on a discriminated union's tag,
    # so there is no bare shorthand to infer one from. Configuration that drives wire
    # values states what it is.
    with pytest.raises(ValidationError):
        build_signal({"path": "vehicle.speed", "value": 1})


# --- constant -------------------------------------------------------------------------------


def test_constant_is_the_same_at_every_time():
    signal = ConstantSignal(path="vehicle.speed", type="constant", value=42)
    assert [signal.value_at(t) for t in (0, 1, 10, 10_000)] == [42, 42, 42, 42]


# --- ramp -----------------------------------------------------------------------------------


def test_ramp_is_linear_between_its_ends():
    signal = build_signal({"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 100, "over": 10})
    assert signal.value_at(0) == 0
    assert signal.value_at(2.5) == 25
    assert signal.value_at(5) == 50
    assert signal.value_at(10) == 100


def test_ramp_holds_its_destination_afterwards():
    signal = build_signal({"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 100, "over": 10})
    assert signal.value_at(10.001) == 100
    assert signal.value_at(1_000_000) == 100


def test_a_ramp_can_go_down():
    signal = build_signal({"path": "engine.coolant_temp", "type": "ramp", "from": 90, "to": 20, "over": 7})
    assert signal.value_at(0) == 90
    assert signal.value_at(3.5) == 55
    assert signal.value_at(7) == 20


def test_a_ramp_needs_a_positive_duration():
    for over in (0, -1):
        with pytest.raises(ValidationError):
            build_signal({"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 1, "over": over})


# --- sine -----------------------------------------------------------------------------------


def test_sine_oscillates_around_its_centre():
    signal = SineSignal(path="engine.rpm", type="sine", centre=1000, amplitude=200, period=4)
    assert signal.value_at(0) == pytest.approx(1000)
    assert signal.value_at(1) == pytest.approx(1200)
    assert signal.value_at(2) == pytest.approx(1000)
    assert signal.value_at(3) == pytest.approx(800)


def test_sine_repeats_every_period():
    signal = SineSignal(path="engine.rpm", type="sine", centre=1000, amplitude=200, period=4)
    assert signal.value_at(0.7) == pytest.approx(signal.value_at(4.7))


def test_sine_phase_shifts_the_wave():
    shifted = SineSignal(path="engine.rpm", type="sine", centre=0, amplitude=1, period=4, phase=math.pi / 2)
    assert shifted.value_at(0) == pytest.approx(1)


def test_sine_needs_a_positive_period():
    with pytest.raises(ValidationError):
        SineSignal(path="engine.rpm", type="sine", centre=1, amplitude=1, period=0)


# --- stepped --------------------------------------------------------------------------------


def test_stepped_walks_its_values_and_repeats():
    signal = SteppedSignal(path="vehicle.speed", type="stepped", values=[10, 20, 30], interval=5)
    assert [signal.value_at(t) for t in (0, 4.999, 5, 9, 10, 14)] == [10, 10, 20, 20, 30, 30]
    assert signal.value_at(15) == 10, "a staircase repeats"
    assert signal.value_at(16) == 10


def test_stepped_lands_exactly_on_its_boundaries():
    signal = SteppedSignal(path="vehicle.speed", type="stepped", values=[0, 1], interval=2)
    assert signal.value_at(2) == 1
    assert signal.value_at(4) == 0


def test_stepped_needs_values_and_a_positive_interval():
    with pytest.raises(ValidationError):
        SteppedSignal(path="vehicle.speed", type="stepped", values=[], interval=5)
    with pytest.raises(ValidationError):
        SteppedSignal(path="vehicle.speed", type="stepped", values=[1], interval=0)


# --- sequence -------------------------------------------------------------------------------


def test_sequence_holds_each_value_for_its_own_duration():
    signal = build_signal(
        {
            "path": "vehicle.speed",
            "type": "sequence",
            "steps": [{"value": 0, "for": 3}, {"value": 50, "for": 2}, {"value": 90, "for": 5}],
        }
    )
    assert [signal.value_at(t) for t in (0, 2.9, 3, 4.9, 5, 9.9)] == [0, 0, 50, 50, 90, 90]


def test_sequence_runs_once_and_holds_its_last_value():
    signal = build_signal(
        {"path": "vehicle.speed", "type": "sequence", "steps": [{"value": 0, "for": 3}, {"value": 50, "for": 2}]}
    )
    assert signal.value_at(5) == 50
    assert signal.value_at(1_000_000) == 50, "a sequence does not repeat; a stepped staircase does"


def test_sequence_needs_at_least_one_step_and_positive_durations():
    with pytest.raises(ValidationError):
        build_signal({"path": "vehicle.speed", "type": "sequence", "steps": []})
    with pytest.raises(ValidationError):
        build_signal({"path": "vehicle.speed", "type": "sequence", "steps": [{"value": 1, "for": 0}]})


# --- timeline -------------------------------------------------------------------------------


def test_timeline_holds_the_latest_point_that_has_arrived():
    signal = build_signal(
        {
            "path": "vehicle.speed",
            "type": "timeline",
            "points": [{"at": 0, "value": 0}, {"at": 10, "value": 60}, {"at": 30, "value": 15}],
        }
    )
    assert [signal.value_at(t) for t in (0, 9.9, 10, 29.9, 30, 300)] == [0, 0, 60, 60, 15, 15]


def test_timeline_uses_its_first_value_before_the_first_point():
    signal = build_signal({"path": "vehicle.speed", "type": "timeline", "points": [{"at": 5, "value": 77}]})
    assert signal.value_at(0) == 77
    assert signal.value_at(4.999) == 77


def test_timeline_points_must_ascend():
    with pytest.raises(ValidationError):
        build_signal(
            {
                "path": "vehicle.speed",
                "type": "timeline",
                "points": [{"at": 10, "value": 1}, {"at": 5, "value": 2}],
            }
        )


def test_timeline_needs_at_least_one_point():
    with pytest.raises(ValidationError):
        build_signal({"path": "vehicle.speed", "type": "timeline", "points": []})


# --- what the byte-exact assertions rest on ---------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [e for e in EVERY_GENERATOR if e["type"] in {"constant", "stepped", "timeline"}],
    ids=lambda e: str(e["type"]),
)
def test_the_generators_used_for_byte_exact_assertions_involve_no_transcendental_function(entry):
    # `sine` goes through libm, where the last bit could in principle differ between
    # platforms and the encoders truncate. Recorded as a risk in docs/decisions/0006
    # section 5.1 rather than discovered on a CI matrix; these three are the ones the
    # wire-level assertions use, and each returns a value it was given.
    signal = build_signal(entry)
    assert float(signal.value_at(7.0)).is_integer()


def test_a_signal_names_the_path_it_drives():
    assert build_signal(EVERY_GENERATOR[0]).path == "vehicle.speed"


@pytest.mark.parametrize("path", ["", "speed", "vehicle.", ".speed"])
def test_a_signal_path_must_be_a_dotted_path(path):
    with pytest.raises(ValidationError):
        build_signal({"path": path, "type": "constant", "value": 1})


def test_generators_are_exported_from_the_package():
    from ecu_simulator import scenario

    assert scenario.ConstantSignal is ConstantSignal
    assert scenario.RampSignal is RampSignal
    assert scenario.SineSignal is SineSignal
    assert scenario.SteppedSignal is SteppedSignal
    assert scenario.SequenceSignal is SequenceSignal
    assert scenario.TimelineSignal is TimelineSignal
