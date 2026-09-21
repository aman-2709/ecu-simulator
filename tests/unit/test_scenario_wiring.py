"""The scenario reaches the wire: the clock, the request-side refresh and the tick.

This is where Phase 7 makes its first production use of the Clock seam Phase 4 built and
never constructed. Three things are asserted here that the runner alone cannot show:

* a diagnostic request brings domain state up to the time that has already passed, before
  the protocol reads it, and does so without advancing anything;
* the periodic tick moves a timed event along when no tester is asking anything;
* both go through the same runner, so an event is consumed once whichever gets there
  first, and the tick shuts down cleanly with the rest of the runtime.
"""

import asyncio
import copy

import pytest

from ecu_simulator import app, cli
from ecu_simulator.clock import MonotonicClock, SimulatedClock
from ecu_simulator.config import load_profile, parse_profile
from ecu_simulator.transport import DiagnosticRequest
from tests.unit.test_config_schema import VALID


def shipped():
    return app.RuntimeConfig.build(load_profile(cli.default_profile_path()))


def with_scenario(scenario=None, dtc_events=None):
    data = copy.deepcopy(VALID)
    if scenario is not None:
        data["scenario"] = scenario
    if dtc_events is not None:
        data["ecus"]["engine"]["dtc_events"] = dtc_events
    return app.RuntimeConfig.build(parse_profile(data))


def runtime(config, clock=None):
    return app.build_runtime(config, clock=clock)


def ask(where, hex_request, address=0x7E0):
    dispatcher = where.dispatcher if isinstance(where, app.Runtime) else where
    response = dispatcher(DiagnosticRequest(bytes.fromhex(hex_request), address))
    return response.payload.hex() if response is not None else None


RAMP = {"signals": [{"path": "vehicle.speed", "type": "ramp", "from": 0, "to": 100, "over": 10}]}


# --- the shipped profile is untouched -------------------------------------------------------


def test_the_shipped_profile_builds_no_runner():
    # No scenario means no runner, no tick and no clock reading on the request path: the
    # default configuration is exactly what it was, by construction and not by coincidence.
    built = runtime(shipped())
    assert built.runner is None and built.sync is None


def test_the_shipped_profile_answers_the_same_bytes_however_much_time_passes():
    clock = SimulatedClock()
    dispatcher = runtime(shipped(), clock)
    first = ask(dispatcher, "010d")
    for _ in range(5):
        clock.advance(3600)
        assert ask(dispatcher, "010d") == first == "410d00"


# --- a request synchronises, it does not advance ----------------------------------------------


def test_a_request_sees_the_state_of_the_moment_it_arrives():
    clock = SimulatedClock()
    dispatcher = runtime(with_scenario(RAMP), clock)
    assert ask(dispatcher, "010d") == "410d00"
    clock.advance(5)
    assert ask(dispatcher, "010d") == "410d32", "50 km/h halfway up the ramp"
    clock.advance(5)
    assert ask(dispatcher, "010d") == "410d64"


def test_asking_twice_at_one_instant_answers_the_same_bytes():
    # The property test_handling_a_request_does_not_mutate_the_vehicle asserts today, kept
    # literally true with a scenario running: a read observes, it never advances.
    clock = SimulatedClock()
    dispatcher = runtime(with_scenario(RAMP), clock)
    clock.advance(3)
    assert len({ask(dispatcher, "010d") for _ in range(20)}) == 1


def test_a_request_does_not_move_the_clock():
    clock = SimulatedClock()
    dispatcher = runtime(with_scenario(RAMP), clock)
    before = clock.now()
    for _ in range(10):
        ask(dispatcher, "010d")
    assert clock.now() == before


def test_the_answer_depends_on_elapsed_time_and_not_on_how_many_requests_arrived():
    clock = SimulatedClock()
    busy = runtime(with_scenario(RAMP), clock)
    for _ in range(100):
        ask(busy, "010d")
    clock.advance(2)
    assert ask(busy, "010d") == "410d14"


def test_the_same_advance_sequence_produces_the_same_bytes_twice():
    def run():
        clock = SimulatedClock()
        dispatcher = runtime(with_scenario(RAMP), clock)
        out = []
        for _ in range(12):
            out.append(ask(dispatcher, "010d"))
            clock.advance(0.9)
        return out

    assert run() == run()


def test_a_scenario_reaches_every_protocol_that_reads_the_signal():
    clock = SimulatedClock()
    dispatcher = runtime(
        with_scenario(
            {
                "signals": [
                    {
                        "path": "engine.coolant_temp",
                        "type": "timeline",
                        "points": [{"at": 0, "value": 90}, {"at": 10, "value": 120}],
                    }
                ]
            }
        ),
        clock,
    )
    assert ask(dispatcher, "0105") == "410582"
    clock.advance(10)
    assert ask(dispatcher, "0105") == "4105a0"


# --- timed events on the request path ------------------------------------------------------


EVENTS = [{"at": 30, "action": "raise_confirmed", "code": "P0001"}]


def test_a_timed_event_becomes_visible_to_both_protocols():
    clock = SimulatedClock()
    dispatcher = runtime(with_scenario(dtc_events=EVENTS), clock)
    assert ask(dispatcher, "03") == "430294770001", "the profile's two codes are confirmed already"
    ask(dispatcher, "14ffffff", 0x7E1)
    assert ask(dispatcher, "03") == "4300"
    clock.advance(30)
    assert ask(dispatcher, "03") == "43010001"
    assert ask(dispatcher, "1902ff", 0x7E1) == "59028c" + "00010108"


def test_an_event_is_not_replayed_after_a_tester_clears_it():
    clock = SimulatedClock()
    dispatcher = runtime(with_scenario(dtc_events=EVENTS), clock)
    clock.advance(30)
    assert ask(dispatcher, "03") == "430294770001"
    ask(dispatcher, "04")
    assert ask(dispatcher, "03") == "4300"
    for seconds in (0, 0, 5, 600):
        clock.advance(seconds)
        assert ask(dispatcher, "03") == "4300", "the event came back"


def test_an_event_is_not_replayed_by_two_requests_at_the_same_instant():
    clock = SimulatedClock()
    dispatcher = runtime(with_scenario(dtc_events=EVENTS), clock)
    clock.advance(30)
    ask(dispatcher, "03")
    ask(dispatcher, "04")
    assert ask(dispatcher, "03") == "4300"


# --- the periodic tick -----------------------------------------------------------------------


def test_no_tick_task_runs_without_a_scenario():
    built = runtime(shipped())

    async def exercise():
        before = len(asyncio.all_tasks())
        async with app.scenario_tick(built.sync, period=0.001):
            await asyncio.sleep(0.01)
            return len(asyncio.all_tasks()) - before

    assert asyncio.run(exercise()) == 0


def test_the_tick_applies_events_with_no_request_in_sight():
    clock = SimulatedClock()
    built = runtime(with_scenario(dtc_events=EVENTS), clock)

    async def exercise():
        async with app.scenario_tick(built.sync, period=0.001):
            clock.advance(30)
            for _ in range(200):
                await asyncio.sleep(0.001)
                if built.runner.pending_events == 0:
                    return True
        return False

    assert asyncio.run(exercise()) is True
    assert tuple(s.code for s in built.ecus[0].dtc_store.confirmed) == ("B1477", "P0001")


def test_the_tick_and_a_request_share_one_runner_so_an_event_is_applied_once():
    clock = SimulatedClock()
    dispatcher = runtime(with_scenario(dtc_events=EVENTS), clock)

    async def exercise():
        async with app.scenario_tick(dispatcher.sync, period=0.0005):
            clock.advance(30)
            for _ in range(50):
                ask(dispatcher, "03")
                await asyncio.sleep(0)
            ask(dispatcher, "04")  # a tester clears what the event raised
            for _ in range(50):
                await asyncio.sleep(0.001)
            return ask(dispatcher, "03")

    assert asyncio.run(exercise()) == "4300"


def test_the_tick_is_cancelled_and_awaited_and_leaks_nothing():
    built = runtime(with_scenario(dtc_events=EVENTS), SimulatedClock())

    async def exercise():
        outside = asyncio.all_tasks()
        async with app.scenario_tick(built.sync, period=0.001):
            assert len(asyncio.all_tasks()) == len(outside) + 1
            await asyncio.sleep(0.005)
        return [t for t in asyncio.all_tasks() if t not in outside and not t.done()]

    assert asyncio.run(exercise()) == []


def test_a_tick_that_raises_does_not_stop_the_simulator():
    def explode():
        raise RuntimeError("scenario went wrong")

    async def exercise():
        async with app.scenario_tick(explode, period=0.001):
            await asyncio.sleep(0.01)
        return True

    assert asyncio.run(exercise()) is True


# --- the elapsed-time seam --------------------------------------------------------------------


def test_scenario_time_starts_at_zero_whatever_the_clock_reads():
    # MonotonicClock counts from an arbitrary origin, so a scenario is anchored to when
    # the runtime started; a restart replays it from the configured initial state.
    clock = SimulatedClock(start=123_456.0)
    built = runtime(with_scenario(RAMP), clock)
    built.sync()
    assert built.vehicle.get("vehicle.speed") == 0
    clock.advance(10)
    built.sync()
    assert built.vehicle.get("vehicle.speed") == 100


def test_the_sync_says_where_scenario_time_started_when_printed():
    built = runtime(with_scenario(RAMP), SimulatedClock(start=42.5))
    assert repr(built.sync) == "ScenarioSync(origin=42.5)"


def test_production_takes_a_monotonic_clock_by_default():
    assert isinstance(runtime(shipped()).clock, MonotonicClock)


def test_the_runtime_accepts_an_injected_clock():
    clock = SimulatedClock()
    assert runtime(shipped(), clock).clock is clock


@pytest.mark.parametrize("hex_request", ["010d", "03", "0100"])
def test_a_dispatcher_without_a_scenario_never_reads_the_clock(hex_request):
    class Exploding:
        def now(self):
            raise AssertionError("the clock was read with no scenario configured")

    ask(runtime(shipped(), Exploding()), hex_request)


# --- the tick lives and dies with the runtime ----------------------------------------------------


class _Transport:
    def __init__(self, interface, endpoints):
        self.handler = None

    async def start(self, handler):
        self.handler = handler

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_run_ticks_a_scenario_and_leaves_nothing_running():
    # The Phase 2 lifecycle guarantee, with a tick in the picture: the runtime exits when
    # asked and the tick task is cancelled and awaited on the way out, so SIGINT and
    # SIGTERM keep behaving the way the integration tests already require.
    outside = asyncio.all_tasks()
    stop = asyncio.Event()
    config = with_scenario(dtc_events=EVENTS)
    config.profile.scenario.tick = 0.001

    async def trigger():
        await asyncio.sleep(0.02)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await app.run(
        config,
        stop=stop,
        install_signal_handlers=False,
        transport_factory=_Transport,
        clock=SimulatedClock(),
    )
    assert [t for t in asyncio.all_tasks() if t not in outside and not t.done()] == []


@pytest.mark.asyncio
async def test_run_without_a_scenario_starts_no_tick():
    started = len(asyncio.all_tasks())
    stop = asyncio.Event()
    stop.set()
    await app.run(shipped(), stop=stop, install_signal_handlers=False, transport_factory=_Transport)
    assert len(asyncio.all_tasks()) <= started
