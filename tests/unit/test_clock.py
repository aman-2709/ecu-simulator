"""Clock seam: real time in production, controlled time in tests."""

import time

from ecu_simulator.clock import Clock, MonotonicClock, SimulatedClock


def test_monotonic_clock_moves_forward_and_never_backwards():
    clock = MonotonicClock()
    first = clock.now()
    time.sleep(0.001)
    second = clock.now()
    assert second >= first
    assert isinstance(clock, Clock)


def test_simulated_clock_starts_at_zero_and_only_moves_when_advanced():
    clock = SimulatedClock()
    assert clock.now() == 0.0
    assert clock.now() == 0.0
    clock.advance(2.5)
    assert clock.now() == 2.5
    clock.advance(0.5)
    assert clock.now() == 3.0
    assert isinstance(clock, Clock)


def test_simulated_clock_can_start_elsewhere_and_rejects_going_backwards():
    clock = SimulatedClock(start=100.0)
    assert clock.now() == 100.0
    try:
        clock.advance(-1.0)
    except ValueError as error:
        assert "negative" in str(error)
    else:
        raise AssertionError("advancing by a negative amount must be rejected")
