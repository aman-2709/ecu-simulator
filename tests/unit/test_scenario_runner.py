"""ScenarioRunner: the only thing that writes what a generator produces.

Two properties carry the whole design and each is asserted directly rather than argued
for in a comment:

* **Idempotence.** ``apply(t)`` twice at the same ``t`` leaves identical state. Signals
  get this for free, being pure functions of ``t``; timed DTC events do not, so each
  carries an applied-marker and can never be applied a second time -- not by a repeated
  request, not by the periodic tick landing on the same instant, and not after a
  diagnostic clear has undone its effect.
* **A read observes, it does not advance.** Nothing here moves the clock. Handling a
  request synchronises domain state to the time that has already passed; it never derives
  state from how many requests have arrived.

See docs/decisions/0006 sections 5 and 6.
"""

import asyncio
import inspect
import logging

import pytest

from ecu_simulator.clock import SimulatedClock
from ecu_simulator.dtc import DtcState, DtcStore
from ecu_simulator.scenario import build_signal
from ecu_simulator.scenario.events import DtcEvent
from ecu_simulator.scenario.runner import Scenario, ScenarioRunner
from ecu_simulator.vehicle import CommonState, IcePowertrain, VehicleState


def vehicle():
    return VehicleState(CommonState(vin="TESTVIN0123456789", speed=0), IcePowertrain())


def store():
    return DtcStore((DtcState("P0001"), DtcState("B1477")))


def event(at, action, code=None):
    return DtcEvent.model_validate({"at": at, "action": action, **({"code": code} if code else {})})


def runner(signals=(), events=(), state=None, stores=None):
    state = state if state is not None else vehicle()
    stores = stores if stores is not None else {"engine": store()}
    scenario = Scenario(
        signals=tuple(build_signal(entry) for entry in signals),
        events=tuple(events),
    )
    return ScenarioRunner(scenario, state, stores), state, stores


# --- signals reach the vehicle -----------------------------------------------------------------


def test_applying_a_scenario_writes_the_generator_value_into_the_vehicle():
    run, state, _ = runner([{"path": "vehicle.speed", "type": "timeline", "points": [{"at": 5, "value": 60}]}])
    run.apply(0.0)
    assert state.get("vehicle.speed") == 60


def test_a_signal_follows_time():
    run, state, _ = runner(
        [{"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 100, "over": 10}]
    )
    for t, expected in ((0, 0), (2.5, 25), (10, 100), (99, 100)):
        run.apply(t)
        assert state.get("vehicle.speed") == expected


def test_an_integer_signal_stays_an_integer():
    # vehicle.speed is whole km/h and the encoder writes it into one byte. A generator
    # produces a float; the runner puts back the type the signal already had, so a ramp
    # through 37.5 does not leave a float where an int belongs.
    run, state, _ = runner([{"path": "vehicle.speed", "type": "constant", "value": 37.5}])
    run.apply(0.0)
    assert state.get("vehicle.speed") == 37
    assert isinstance(state.get("vehicle.speed"), int)


def test_a_float_signal_stays_a_float():
    run, state, _ = runner([{"path": "engine.coolant_temp", "type": "constant", "value": 91.5}])
    run.apply(0.0)
    assert state.get("engine.coolant_temp") == pytest.approx(91.5)


def test_a_signal_the_vehicle_does_not_have_is_refused_when_the_runner_is_built():
    # A bev has no engine. The runner refuses to exist rather than raising on the first
    # request; profile validation catches this earlier still.
    with pytest.raises(KeyError):
        runner([{"path": "battery.soc", "type": "constant", "value": 50}])


def test_two_signals_may_not_drive_the_same_path():
    with pytest.raises(ValueError, match="vehicle.speed"):
        runner(
            [
                {"path": "vehicle.speed", "type": "constant", "value": 1},
                {"path": "vehicle.speed", "type": "constant", "value": 2},
            ]
        )


def test_applying_the_same_time_twice_leaves_identical_state():
    run, state, _ = runner([{"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 100, "over": 10}])
    run.apply(3.0)
    first = dict(state.signals)
    run.apply(3.0)
    assert dict(state.signals) == first


def test_a_scenario_with_no_signals_and_no_events_changes_nothing():
    run, state, stores = runner()
    before = dict(state.signals)
    for t in (0.0, 1.0, 1000.0):
        run.apply(t)
    assert dict(state.signals) == before
    assert stores["engine"].pending == ()


# --- timed DTC events --------------------------------------------------------------------------


def test_an_event_fires_once_its_time_has_arrived():
    run, _, stores = runner(events=[("engine", event(10, "raise_confirmed", "P0001"))])
    run.apply(9.999)
    assert stores["engine"].confirmed == ()
    run.apply(10.0)
    assert tuple(s.code for s in stores["engine"].confirmed) == ("P0001",)


def test_an_event_exactly_on_its_boundary_fires():
    run, _, stores = runner(events=[("engine", event(10, "raise_pending", "P0001"))])
    run.apply(10.0)
    assert tuple(s.code for s in stores["engine"].pending) == ("P0001",)


def test_each_action_sets_one_flag_and_leaves_the_others_alone():
    run, _, stores = runner(
        events=[
            ("engine", event(1, "raise_pending", "P0001")),
            ("engine", event(2, "raise_confirmed", "B1477")),
            ("engine", event(3, "request_indicator", "P0001")),
        ]
    )
    run.apply(3.0)
    engine = stores["engine"]
    assert tuple(s.code for s in engine.pending) == ("P0001",)
    assert tuple(s.code for s in engine.confirmed) == ("B1477",)
    assert engine.indicator_on is True


def test_clear_code_clears_only_its_own_code():
    run, _, stores = runner(
        events=[
            ("engine", event(1, "raise_confirmed", "P0001")),
            ("engine", event(1, "raise_confirmed", "B1477")),
            ("engine", event(2, "clear_code", "P0001")),
        ]
    )
    run.apply(2.0)
    assert tuple(s.code for s in stores["engine"].confirmed) == ("B1477",)


def test_clear_all_empties_the_store_it_names():
    run, _, stores = runner(
        events=[("engine", event(1, "raise_confirmed", "P0001")), ("engine", event(2, "clear_all"))]
    )
    run.apply(2.0)
    assert stores["engine"].confirmed == () and stores["engine"].pending == ()


def test_a_time_jump_applies_every_event_it_crossed_in_order():
    run, _, stores = runner(
        events=[
            ("engine", event(1, "raise_confirmed", "P0001")),
            ("engine", event(2, "raise_confirmed", "B1477")),
            ("engine", event(3, "clear_code", "P0001")),
        ]
    )
    run.apply(1000.0)
    # All three fired, in their configured order, so the clear came after the raise.
    assert tuple(s.code for s in stores["engine"].confirmed) == ("B1477",)


def test_events_sharing_a_time_are_applied_in_configuration_order():
    run, _, stores = runner(
        events=[
            ("engine", event(5, "raise_confirmed", "P0001")),
            ("engine", event(5, "clear_all")),
            ("engine", event(5, "raise_pending", "B1477")),
        ]
    )
    run.apply(5.0)
    assert stores["engine"].confirmed == ()
    assert tuple(s.code for s in stores["engine"].pending) == ("B1477",)


def test_an_event_naming_an_ecu_with_no_store_is_refused_when_the_runner_is_built():
    with pytest.raises(KeyError, match="gearbox"):
        runner(events=[("gearbox", event(1, "raise_pending", "P0001"))])


def test_an_event_naming_a_code_the_ecu_does_not_carry_is_refused_when_the_runner_is_built():
    # DtcStore.update raises KeyError for an unconfigured code by design. A scenario that
    # would trip that is refused before a socket opens, not at the instant it fires.
    with pytest.raises(KeyError, match="P9999"):
        runner(events=[("engine", event(1, "raise_pending", "P9999"))])


# --- event idempotence, the part that has to be provably impossible to get wrong ------------


def test_applying_the_same_time_twice_does_not_apply_an_event_twice():
    run, _, stores = runner(events=[("engine", event(10, "raise_confirmed", "P0001"))])
    run.apply(10.0)
    stores["engine"].clear()
    run.apply(10.0)
    assert stores["engine"].confirmed == (), "the event was replayed at the same instant"


def test_an_event_is_not_replayed_after_a_diagnostic_clear():
    # The sequence the register calls out: the event becomes due, it raises the code, a
    # tester clears it, and a later request must not bring it back.
    run, _, stores = runner(events=[("engine", event(10, "raise_confirmed", "P0001"))])
    run.apply(10.0)
    assert tuple(s.code for s in stores["engine"].confirmed) == ("P0001",)
    stores["engine"].clear()
    for t in (10.0, 10.5, 60.0, 10_000.0):
        run.apply(t)
        assert stores["engine"].confirmed == (), f"the event came back at t={t}"


def test_applying_the_same_time_many_times_applies_each_event_once():
    run, _, stores = runner(events=[("engine", event(0, "raise_pending", "P0001"))])
    for _ in range(50):
        run.apply(0.0)
    stores["engine"].clear()
    run.apply(0.0)
    assert stores["engine"].pending == ()


def test_a_clock_that_does_not_advance_produces_no_second_application():
    clock = SimulatedClock()
    run, _, stores = runner(events=[("engine", event(0, "raise_confirmed", "P0001"))])
    run.apply(clock.now())
    stores["engine"].clear()
    for _ in range(10):
        run.apply(clock.now())
    assert stores["engine"].confirmed == ()


def test_the_runner_reports_which_events_it_has_consumed():
    # The marker is inspectable, so a test can say "this event has been applied" rather
    # than inferring it from state that a clear may have undone.
    run, _, _ = runner(
        events=[("engine", event(1, "raise_pending", "P0001")), ("engine", event(5, "raise_pending", "B1477"))]
    )
    assert run.pending_events == 2
    run.apply(1.0)
    assert run.pending_events == 1
    run.apply(5.0)
    assert run.pending_events == 0


# --- time never runs backwards ------------------------------------------------------------------


def test_the_simulated_clock_cannot_be_advanced_backwards():
    # The simpler of the two designs the clock abstraction allowed: advancement is
    # monotonic at the source, so the runner never sees a backward t from a test either.
    with pytest.raises(ValueError):
        SimulatedClock().advance(-1)


def test_a_backward_time_is_refused_rather_than_reinterpreted(caplog):
    run, state, _ = runner([{"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 100, "over": 10}])
    run.apply(8.0)
    with caplog.at_level(logging.WARNING):
        run.apply(3.0)
    assert state.get("vehicle.speed") == 80, "state was rewound"
    assert "backwards" in caplog.text.lower()


def test_a_backward_time_does_not_release_a_consumed_event():
    run, _, stores = runner(events=[("engine", event(5, "raise_confirmed", "P0001"))])
    run.apply(10.0)
    stores["engine"].clear()
    run.apply(1.0)
    assert stores["engine"].confirmed == ()


def test_the_last_applied_time_only_moves_forward():
    run, _, _ = runner()
    run.apply(5.0)
    run.apply(2.0)
    assert run.last_applied == 5.0
    run.apply(7.0)
    assert run.last_applied == 7.0


# --- the concurrency model ---------------------------------------------------------------------


def test_apply_is_synchronous():
    # The whole serialization argument rests on this. `apply` is an ordinary function, so
    # on one event loop it runs to completion without yielding: the periodic tick and a
    # request can interleave between calls but never inside one, and no lock is needed.
    # If this ever becomes a coroutine, the shared cursor needs a serialization mechanism
    # and this test is the place that says so.
    assert not inspect.iscoroutinefunction(ScenarioRunner.apply)
    assert not inspect.isasyncgenfunction(ScenarioRunner.apply)


def test_no_await_can_be_hidden_inside_apply():
    source = inspect.getsource(ScenarioRunner)
    assert "await " not in source
    assert "async def" not in source


def test_interleaving_a_tick_and_a_request_at_the_same_time_applies_an_event_once():
    run, _, stores = runner(events=[("engine", event(1, "raise_confirmed", "P0001"))])

    async def exercise():
        async def tick():
            for _ in range(20):
                run.apply(1.0)
                await asyncio.sleep(0)

        async def requests():
            for _ in range(20):
                run.apply(1.0)
                await asyncio.sleep(0)

        await asyncio.gather(tick(), requests())

    asyncio.run(exercise())
    stores["engine"].clear()
    run.apply(1.0)
    assert stores["engine"].confirmed == ()


def test_a_request_side_apply_then_a_tick_at_the_same_time_applies_an_event_once():
    run, _, stores = runner(events=[("engine", event(4, "raise_pending", "P0001"))])
    run.apply(4.0)  # the request path
    stores["engine"].clear()
    run.apply(4.0)  # the tick, at the same instant
    assert stores["engine"].pending == ()


def test_a_tick_then_a_request_side_apply_at_the_same_time_applies_an_event_once():
    run, _, stores = runner(events=[("engine", event(4, "raise_pending", "P0001"))])
    run.apply(4.0)  # the tick
    stores["engine"].clear()
    run.apply(4.0)  # the request path, at the same instant
    assert stores["engine"].pending == ()


# --- what a scenario is not allowed to do --------------------------------------------------------


def test_the_scenario_package_imports_no_protocol_or_transport_module():
    from tests.unit.test_transport_isolation import loaded_modules

    loaded = loaded_modules("ecu_simulator.scenario", "ecu_simulator.scenario.runner")
    leaked = [
        m
        for m in loaded
        if m.startswith(("ecu_simulator.protocols", "ecu_simulator.transport", "ecu_simulator.ecu"))
    ]
    assert leaked == [], f"scenario reached the protocol or transport layers: {leaked}"


def test_the_runner_writes_only_through_the_domain_apis():
    source = inspect.getsource(ScenarioRunner)
    for forbidden in ("_entries", "setattr(", "object.__setattr__"):
        assert forbidden not in source, f"the runner reaches past a domain API with {forbidden!r}"


def test_a_scenario_never_moves_the_clock():
    # Nothing in this project advances time. The runner does not even import a clock: it
    # is told what `t` is, which is what makes a SimulatedClock enough to reproduce a
    # whole run and what stops a request from becoming a source of time.
    from tests.unit.test_transport_isolation import loaded_modules

    assert "ecu_simulator.clock" not in loaded_modules("ecu_simulator.scenario.runner")
