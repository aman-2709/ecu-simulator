"""A whole car on vcan, run through the GUI's own child process and checked by an independent receiver.

The receiver is a raw SocketCAN socket opened in the test with kernel receive timestamps (SO_TIMESTAMPNS) and the
kernel's receive-queue overflow counter (SO_RXQ_OVFL), not the simulator's capture code. Counters are read with a
Motorola bit reader written here from the DBC text, not with the production ``BitField``. The run's own metadata comes
from the ready event, through ``Manager.status()``, as the GUI receives it.
"""

import asyncio
import hashlib
import re
import socket
import statistics
import struct
import time
from itertools import pairwise
from typing import Any

import pytest

from ecu_simulator.car.catalog import bundled_dbc_path, load_catalog
from ecu_simulator.cli import default_profile_path
from ecu_simulator.dbc import DbcStore
from ecu_simulator.gui.manager import Manager
from tests.integration.conftest import open_tester_socket
from tests.unit.test_car_run import many_messages, synthetic_dbc

TIMESTAMP_NS = getattr(socket, "SO_TIMESTAMPNS", 35)
RXQ_OVFL = getattr(socket, "SO_RXQ_OVFL", 40)
EFF_FLAG, EFF_MASK, SFF_MASK = 0x8000_0000, 0x1FFF_FFFF, 0x7FF
RECORD = struct.Struct("=IB3x8s")
DEMO = default_profile_path().with_name("car_toyota_demo.yaml")


# --- an independent receiver ------------------------------------------------------------------------------------------


def open_receiver(interface: str, can_filter: tuple[int, int] | None = None) -> socket.socket:
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    if can_filter is not None:
        sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, struct.pack("=II", *can_filter))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    sock.setsockopt(socket.SOL_SOCKET, TIMESTAMP_NS, 1)
    sock.setsockopt(socket.SOL_SOCKET, RXQ_OVFL, 1)
    sock.settimeout(0.1)
    sock.bind((interface,))
    return sock


def receive(sock: socket.socket) -> tuple[int, int, bool, bytes, int | None]:
    """One frame as (kernel time in ns, identifier, extended, data, overflow count or None)."""
    space = socket.CMSG_SPACE(struct.calcsize("@ll")) + socket.CMSG_SPACE(4)
    raw, ancillary, _, _ = sock.recvmsg(16, space)
    word, length, data = RECORD.unpack(raw)
    stamp, overflow = None, None
    for level, kind, value in ancillary:
        if (level, kind) == (socket.SOL_SOCKET, TIMESTAMP_NS):
            seconds, nanos = struct.unpack("@ll", value)
            stamp = seconds * 10**9 + nanos
        elif (level, kind) == (socket.SOL_SOCKET, RXQ_OVFL):
            overflow = struct.unpack("=I", value)[0]
    assert stamp is not None
    extended = bool(word & EFF_FLAG)
    return stamp, word & (EFF_MASK if extended else SFF_MASK), extended, data[:length], overflow


def collect(sock: socket.socket, seconds: float) -> tuple[list[tuple[int, int, bool, bytes]], int]:
    frames, overflow = [], 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            stamp, can_id, extended, data, dropped = receive(sock)
        except TimeoutError:
            continue
        frames.append((stamp, can_id, extended, data))
        overflow = dropped if dropped is not None else overflow
    return frames, overflow


def motorola(data: bytes, start: int, length: int) -> int:
    """A big-endian DBC field: ``start`` is its most significant bit in the DBC's sawtooth bit numbering."""
    value, position = 0, start
    for _ in range(length):
        value = value << 1 | (data[position // 8] >> (position % 8)) & 1
        position = position + 15 if position % 8 == 0 else position - 1
    return value


def dbc_signal(text: str, message: str, signal: str) -> tuple[int, int, float, float]:
    """(start, length, factor, offset) of a Motorola unsigned signal, read from the DBC text."""
    block = re.search(rf"^BO_ \d+ {message}: .*?(?=^BO_ |\Z)", text, re.M | re.S)
    assert block is not None, message
    found = re.search(rf"^ SG_ {signal} : (\d+)\|(\d+)@0\+ \(([-\d.]+),([-\d.]+)\)", block.group(0), re.M)
    assert found is not None, (message, signal)
    return int(found[1]), int(found[2]), float(found[3]), float(found[4])


def timing(stamps: list[int]) -> dict[str, float]:
    intervals = [(later - earlier) / 1e6 for earlier, later in pairwise(stamps)]
    return {
        "count": len(stamps),
        "median": statistics.median(intervals),
        "p95": statistics.quantiles(intervals, n=100, method="inclusive")[94],
        "max": max(intervals),
    }


# --- the run, as the GUI starts it ------------------------------------------------------------------------------------


async def running(manager: Manager, text: str, timeout: float = 10) -> dict[str, Any]:
    await manager.start(text)
    async with asyncio.timeout(timeout):
        while (status := manager.status())["state"] != "Running":
            assert status["state"] != "Failed", status["error"]
            await asyncio.sleep(0.02)
    return status


def by_id(car: dict[str, Any]) -> dict[tuple[int, bool], dict[str, Any]]:
    return {(m["id"], m["extended"]): m for m in car["messages"] if m["skipped"] is None}


def check_counters(frames: list[tuple[int, int, bool, bytes]], key: tuple[int, bool], field: tuple[int, int]) -> int:
    """Assert the counter of every frame of ``key`` is the previous one plus one, wrapping; return the wraps seen."""
    start, length = field
    values = [motorola(data, start, length) for _, can_id, extended, data in frames if (can_id, extended) == key]
    assert values and values[0] == 0, values[:3]
    top = (1 << length) - 1
    for earlier, later in pairwise(values):
        assert later == (0 if earlier == top else earlier + 1), (key, earlier, later)
    return sum(1 for earlier, later in pairwise(values) if later < earlier)


@pytest.mark.asyncio
async def test_corolla_run_on_vcan_matches_independent_capture_and_holds_period(vcan):
    manager = Manager()
    # The run's only counter (STEERING_LKA, 6 bits at an assumed 100 ms) wraps after 6.4 s; capture past one wrap.
    seconds = 7.0
    with open_receiver(vcan) as sock:
        try:
            status = await running(manager, DEMO.read_text())
            frames, overflow = await asyncio.to_thread(collect, sock, seconds)
        finally:
            await manager.close()
    car = status["car"]
    assert status["dbc"] == {"filename": car["dbc"]["filename"], "sha256": car["dbc"]["sha256"], "messages": []}
    sent = by_id(car)
    assert overflow == 0
    received: dict[tuple[int, bool], list[int]] = {}
    for stamp, can_id, extended, data in frames:
        assert (can_id, extended) in sent, hex(can_id)
        assert len(data) == sent[can_id, extended]["dlc"], hex(can_id)
        received.setdefault((can_id, extended), []).append(stamp)
    assert received.keys() == sent.keys()

    fastest = min(sent, key=lambda key: sent[key]["period_ms"])
    stated = sent[fastest]["period_ms"]
    measured = timing(received[fastest])
    print(f"{vcan} {sent[fastest]['name']} ({stated} ms, {seconds} s): {measured}")
    slowest = timing(received[max(sent, key=lambda key: sent[key]["period_ms"])])
    print(f"{vcan} total frames {len(frames)} over {seconds} s; a 100 ms message: {slowest}")
    assert abs(measured["median"] - stated) <= 0.1 * stated

    text = bundled_dbc_path(car["dbc"]["sha256"]).read_text()
    counted = [key for key in sent if sent[key]["counters"]]
    assert [sent[key]["name"] for key in counted] == ["STEERING_LKA"]
    start, length, _, _ = dbc_signal(text, "STEERING_LKA", "COUNTER")
    assert check_counters(frames, counted[0], (start, length)) >= 1


@pytest.mark.asyncio
async def test_150_message_car_keeps_period_on_vcan(vcan, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    raw = synthetic_dbc(many_messages(150, 10))
    DbcStore().import_file("many.dbc", raw)
    demo_car = "car:\n  platform: TOYOTA_COROLLA\n  bus: pt\n"
    assert demo_car in DEMO.read_text()
    car = f"car:\n  sha256: {hashlib.sha256(raw).hexdigest()}\n  filename: many.dbc\n  rule: toyota\n"
    text = DEMO.read_text().replace(demo_car, car)
    manager = Manager()
    seconds = 5.0
    with open_receiver(vcan) as sock:
        try:
            status = await running(manager, text)
            started = time.monotonic()
            frames, overflow = await asyncio.to_thread(collect, sock, seconds)
            elapsed = time.monotonic() - started
        finally:
            await manager.close()
    sent = by_id(status["car"])
    assert len(sent) == 150 and {m["period_ms"] for m in sent.values()} == {10}
    received: dict[tuple[int, bool], list[int]] = {}
    for stamp, can_id, extended, _data in frames:
        received.setdefault((can_id, extended), []).append(stamp)
    assert received.keys() == sent.keys()

    results = {key: timing(stamps) for key, stamps in received.items()}
    medians = sorted(r["median"] for r in results.values())
    expected = elapsed / 0.010
    counts = sorted(r["count"] for r in results.values())
    print(
        f"{vcan} 150 x 10 ms over {elapsed:.2f} s: frames {len(frames)} (expected about {150 * expected:.0f}), "
        f"capture overflow {overflow}; per message count min {counts[0]} median {statistics.median(counts)} "
        f"max {counts[-1]}; median period min {medians[0]:.3f} median {statistics.median(medians):.3f} "
        f"max {medians[-1]:.3f} ms; p95 worst {max(r['p95'] for r in results.values()):.3f} ms; "
        f"max interval {max(r['max'] for r in results.values()):.3f} ms"
    )
    assert overflow == 0
    for key, result in results.items():
        assert abs(result["median"] - 10) <= 1.0, (sent[key]["name"], result)
    for key in (key for key in sent if sent[key]["counters"]):
        check_counters(frames, key, (55, 4))


@pytest.mark.asyncio
async def test_obd_speed_matches_wheel_speed_plateau(vcan):
    entry = load_catalog().dbcs[load_catalog().platforms["TOYOTA_COROLLA"].buses["pt"]]
    start, length, factor, offset = dbc_signal(bundled_dbc_path(entry.sha256).read_text(), "WHEEL_SPEEDS", "WHEEL_SPEED_FR")

    def wheel_speed(data: bytes) -> float:
        return round(motorola(data, start, length) * factor + offset, 2)

    def wait_for(sock: socket.socket, speed: float, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                _, _, _, data, _ = receive(sock)
            except TimeoutError:
                continue
            if wheel_speed(data) == speed:
                return
        pytest.fail(f"no WHEEL_SPEEDS frame at {speed} km/h within {timeout} s")

    manager = Manager()
    with open_receiver(vcan, (0xAA, socket.CAN_SFF_MASK | socket.CAN_EFF_FLAG | socket.CAN_RTR_FLAG)) as sock:
        try:
            await running(manager, DEMO.read_text())
            # The demo steps vehicle.speed 0, 40, 0 every 10 s; the plateau at 40 starts 10 s into the run.
            await asyncio.to_thread(wait_for, sock, 40.0, 15)
            tester = open_tester_socket(vcan, rx_id=0x7E8, tx_id=0x7E0)
            try:
                tester.send(b"\x01\x0d")
                answer = await asyncio.to_thread(tester.recv)
            finally:
                tester.close()
            _, _, _, after, _ = await asyncio.to_thread(receive, sock)
        finally:
            await manager.close()
    assert answer == bytes([0x41, 0x0D, 40])
    assert wheel_speed(after) == 40.0
