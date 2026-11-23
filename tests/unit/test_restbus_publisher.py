import asyncio
import itertools
import struct
from unittest.mock import Mock

import pytest

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.clock import SimulatedClock
from ecu_simulator.config import ConfigError, load_profile, load_yaml, parse_profile
from ecu_simulator.transport import TransportIOError

DEMO = default_profile_path().with_name("wheel_speed_demo.yaml")


def test_demo_uses_repeatable_plateaus_and_existing_diagnostics():
    from ecu_simulator.transport import DiagnosticRequest

    clock = SimulatedClock()
    config = app.RuntimeConfig.build(load_profile(DEMO))
    assert config.profile.restbus.period_ms == 20
    assert load_profile(default_profile_path()).restbus is None
    runtime = app.build_runtime(config, clock)
    for speed in [0, 40, 0, 0, 40]:
        reply = runtime.dispatcher(DiagnosticRequest(bytes.fromhex("010D"), 0x7E0))
        assert reply.payload == bytes([0x41, 0x0D, speed])
        clock.advance(10)


@pytest.mark.parametrize(
    "field,value", [("dbc", "typo"), ("signals", ["FL"]), ("source", "engine.rpm"), ("period_ms", 1)]
)
def test_demo_rejects_unsupported_mapping(field, value):
    data = load_yaml(DEMO)
    data["restbus"][field] = value
    with pytest.raises(ConfigError, match=field):
        parse_profile(data)


@pytest.mark.parametrize("field", ["rx", "tx"])
def test_broadcast_identifier_must_be_free(field):
    data = load_yaml(DEMO)
    data["ecus"]["engine"]["endpoints"][0][field] = 0xAA
    with pytest.raises(ConfigError, match="0x0AA"):
        parse_profile(data)


def test_schedule_preserves_phase_and_skips_elapsed_slots():
    from ecu_simulator.publisher import next_deadline

    assert next_deadline(1, 1.015, 0.02) == pytest.approx(1.02)
    assert next_deadline(1, 1.099, 0.02) == pytest.approx(1.10)
    assert next_deadline(1, 1.100, 0.02) == pytest.approx(1.12)


def test_first_offsets_spread_each_period_group_across_its_period():
    from ecu_simulator.publisher import first_offsets

    periods = [0.1] * 55 + [0.012, 0.05, 0.05, 0.1, 0.012]
    offsets = first_offsets(periods)
    assert offsets == first_offsets(periods)  # deterministic: no randomness, no clock
    assert len(offsets) == len(periods)
    assert all(0 <= offset < period for offset, period in zip(offsets, periods, strict=True))
    for period in set(periods):
        group = [offset for offset, p in zip(offsets, periods, strict=True) if p == period]
        assert len(set(group)) == len(group), period  # no two frames of one period share a first deadline
    hundred = sorted(offset for offset, p in zip(offsets, periods, strict=True) if p == 0.1)
    assert hundred == pytest.approx([0.1 * k / 56 for k in range(56)])  # evenly spread, in list order
    assert first_offsets([0.02]) == [0.0] and first_offsets([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("stagger", "first_tick"), [(False, ["A", "B"]), (True, ["A"])])
async def test_publish_frames_staggers_first_deadlines_only_when_asked(monkeypatch, stagger, first_tick):
    """Two frames of one period start together by default (every existing profile); a staggered car run sends the
    second half a period later, and both keep their period afterwards."""
    from ecu_simulator import publisher

    loop = asyncio.get_running_loop()
    sent: list[tuple[str, float]] = []
    stop = asyncio.Event()
    start = loop.time()

    def sample(name: str):
        def payload() -> bytes:
            sent.append((name, loop.time() - start))
            if len(sent) == 6:
                stop.set()
            return b"\x01"

        return payload

    sock = Mock()
    sock.send.side_effect = lambda data: len(data)
    monkeypatch.setattr(publisher, "open_broadcast", lambda _: sock)
    frames = [publisher.CyclicFrame(name, 0x100 + i, 0.04, sample(name)) for i, name in enumerate("AB")]
    ready = Mock(side_effect=lambda: sent.append(("ready", loop.time() - start)))
    await asyncio.wait_for(publisher.publish_frames("vcan0", frames, None, ready, stop, stagger=stagger), 2)
    names = [name for name, _ in sent]
    assert names[: names.index("ready")] == first_tick  # what the first tick sent, before ready is announced
    sent = [entry for entry in sent if entry[0] != "ready"]
    times = {name: [at for n, at in sent if n == name] for name in "AB"}
    b_start = times["B"][0] - times["A"][0]
    assert b_start == pytest.approx(0.02 if stagger else 0.0, abs=0.012)
    for name in "AB":
        intervals = [later - earlier for earlier, later in itertools.pairwise(times[name])]
        assert intervals and all(interval == pytest.approx(0.04, abs=0.015) for interval in intervals), intervals


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", [1, 2])
async def test_transmit_failure_surfaces_and_closes_raw_and_diagnostic_sockets(monkeypatch, fail_at):
    from ecu_simulator import publisher

    frames = []

    def send(data):
        frames.append(data)
        if len(frames) == fail_at:
            raise OSError("device down")
        return len(data)

    sock = Mock()
    sock.send.side_effect = send
    monkeypatch.setattr(publisher, "open_broadcast", lambda interface: sock)
    transport = FakeTransport()
    codec = Mock()
    codec.encode.return_value = bytes.fromhex("1A6F" * 4)
    ready = Mock()
    with pytest.raises(TransportIOError, match="device down"):
        await asyncio.wait_for(
            app.run(
                app.RuntimeConfig.build(load_profile(DEMO)),
                ready=ready,
                transport_factory=lambda *_: transport,
                install_signal_handlers=False,
                wheel_codec=codec,
            ),
            timeout=1,
        )
    assert ready.call_count == fail_at - 1
    assert transport.closed
    sock.close.assert_called_once()
    assert frames[0] == struct.pack("=IB3x8s", 0xAA, 8, bytes.fromhex("1A6F" * 4))


class FakeTransport:
    closed = False

    async def start(self, handler):
        self.handler = handler

    async def stop(self):
        self.closed = True


@pytest.mark.asyncio
async def test_paused_run_resumes_with_current_scenario_speed(monkeypatch):
    from ecu_simulator import publisher
    from ecu_simulator.transport import DiagnosticRequest

    paused = True
    clock = SimulatedClock()
    stop = asyncio.Event()
    ready = asyncio.Event()
    codec = Mock()
    codec.encode.return_value = bytes.fromhex("2A0F" * 4)
    sock = Mock()

    def send(data):
        stop.set()
        return len(data)

    sock.send.side_effect = send
    monkeypatch.setattr(publisher, "open_broadcast", lambda interface: sock)
    transport = FakeTransport()
    task = asyncio.create_task(
        app.run(
            app.RuntimeConfig.build(load_profile(DEMO)),
            ready=ready.set,
            stop=stop,
            clock=clock,
            transport_factory=lambda *_: transport,
            install_signal_handlers=False,
            wheel_codec=codec,
            broadcast_paused=lambda: paused,
        )
    )
    await asyncio.wait_for(ready.wait(), 1)
    clock.advance(10)
    assert transport.handler(DiagnosticRequest(bytes.fromhex("010D"), 0x7E0)).payload.hex() == "410d28"
    sock.send.assert_not_called()
    clock.advance(10)  # No further diagnostic read before the first resumed frame.
    paused = False
    await asyncio.wait_for(task, 1)
    codec.encode.assert_called_once_with(0)
    sock.close.assert_called_once()


@pytest.mark.asyncio
async def test_bind_failure_closes_both_sockets_before_ready(monkeypatch):
    from ecu_simulator import publisher

    sock = Mock()
    sock.bind.side_effect = OSError("no such device")
    monkeypatch.setattr(publisher.socket, "socket", Mock(return_value=sock))
    transport = FakeTransport()
    ready = Mock()
    with pytest.raises(TransportIOError, match="no such device"):
        await app.run(
            app.RuntimeConfig.build(load_profile(DEMO)),
            ready=ready,
            transport_factory=lambda *_: transport,
            install_signal_handlers=False,
        )
    sock.close.assert_called_once()
    assert transport.closed
    ready.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_stops_every_socket(monkeypatch):
    from ecu_simulator import publisher

    ready = asyncio.Event()
    sock = Mock()
    sock.send.side_effect = len
    transport = FakeTransport()
    monkeypatch.setattr(publisher, "open_broadcast", lambda _: sock)
    task = asyncio.create_task(
        app.run(
            app.RuntimeConfig.build(load_profile(DEMO)),
            ready=ready.set,
            transport_factory=lambda *_: transport,
            install_signal_handlers=False,
        )
    )
    await asyncio.wait_for(ready.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sock.close.assert_called_once()
    assert transport.closed
