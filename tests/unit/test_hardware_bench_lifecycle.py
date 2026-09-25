"""The physical backend owns the simulator, and never starts it on an occupied bus.

The bench refusal in tests/hardware exists to stop this simulator transmitting on a live
vehicle bus (docs/decisions/0008 section 2). It used to run while the operator's own
simulator was already up, so it saw that simulator, called the bench occupied and failed
every physical case. The suite now starts the simulator itself, after the check.

These tests pin the order with fakes and touch no CAN interface: check, then start; check
again before every restart; stop whatever happens.
"""

from __future__ import annotations

import pytest

from tests.hardware.bench_simulator import BenchOccupied, BenchSimulator


class Recorder:
    """A fake bus probe and a fake simulator writing one shared, ordered event log."""

    def __init__(self, responders: list[bool], ready: bool = True) -> None:
        self.events: list[str] = []
        self._responders = list(responders)
        self._ready = ready

    def responder_present(self, interface: str) -> bool:
        self.events.append(f"check {interface}")
        return self._responders.pop(0)

    def launch(self, interface: str, workdir: str) -> FakeSimulator:
        self.events.append(f"launch {interface}")
        return FakeSimulator(self)


class FakeSimulator:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def wait_ready(self) -> None:
        self.recorder.events.append("wait_ready")
        if not self.recorder._ready:
            raise RuntimeError("simulator did not become ready")

    def terminate(self) -> None:
        self.recorder.events.append("terminate")


def bench(recorder: Recorder) -> BenchSimulator:
    return BenchSimulator(
        "can9", "/nonexistent", responder_present=recorder.responder_present, launch=recorder.launch
    )


def test_a_bus_that_already_answers_is_refused_before_anything_starts():
    recorder = Recorder(responders=[True])
    with pytest.raises(BenchOccupied, match="already answers OBD"):
        with bench(recorder):
            pytest.fail("the body must not run on an occupied bus")
    assert recorder.events == ["check can9"]


def test_the_bus_is_checked_before_the_simulator_is_started():
    recorder = Recorder(responders=[False])
    with bench(recorder):
        assert recorder.events == ["check can9", "launch can9", "wait_ready"]


def test_the_simulator_is_stopped_when_a_test_fails():
    recorder = Recorder(responders=[False])
    with pytest.raises(AssertionError, match="case failed"):
        with bench(recorder):
            raise AssertionError("case failed")
    assert recorder.events[-1] == "terminate"


def test_a_simulator_that_never_becomes_ready_is_still_stopped():
    recorder = Recorder(responders=[False], ready=False)
    with pytest.raises(RuntimeError, match="did not become ready"):
        with bench(recorder):
            pytest.fail("the body must not run without a ready simulator")
    assert recorder.events == ["check can9", "launch can9", "wait_ready", "terminate"]


def test_a_restart_checks_the_bus_again_before_starting():
    recorder = Recorder(responders=[False, False])
    with bench(recorder) as sim:
        sim.restart()
    assert recorder.events == [
        "check can9", "launch can9", "wait_ready",
        "terminate",
        "check can9", "launch can9", "wait_ready",
        "terminate",
    ]  # fmt: skip


def test_a_responder_that_appears_before_a_restart_is_refused_and_nothing_is_left_running():
    # Between two cases something else started answering: the bench is no longer the one
    # the operator named, and the simulator must not come back up on it.
    recorder = Recorder(responders=[False, True])
    with pytest.raises(BenchOccupied):
        with bench(recorder) as sim:
            sim.restart()
    assert recorder.events == ["check can9", "launch can9", "wait_ready", "terminate", "check can9"]


def test_the_default_probe_and_launcher_are_the_real_ones():
    # With nothing injected, the probe must be the integration suite's own responder check
    # and the launcher its Simulator subprocess: a default that skipped the check would
    # pass every test above and still start on a vehicle bus.
    from tests.integration.conftest import Simulator, _foreign_responder_present

    sim = BenchSimulator("can9", "/nonexistent")
    assert sim.responder_present is _foreign_responder_present
    assert sim.launch is Simulator
