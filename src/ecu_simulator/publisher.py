"""Run-scoped Classical CAN publishers on a monotonic schedule."""

import asyncio
import math
import socket
import struct
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from ecu_simulator.transport import TransportIOError

FRAME = struct.Struct("=IB3x8s")


def open_broadcast(interface: str) -> socket.socket:
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    try:
        sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER, b"")
        sock.bind((interface,))
        sock.setblocking(False)
    except BaseException:
        sock.close()
        raise
    return sock


def next_deadline(previous: float, now: float, period: float) -> float:
    """Advance on the original slot grid, dropping every slot that has passed."""
    slots = max(1, math.floor((now - previous) / period) + 1)
    deadline = previous + slots * period
    return deadline if deadline > now else deadline + period


async def publish_wheels(
    interface: str,
    period: float,
    sample: Callable[[], bytes],
    paused: Callable[[], bool] | None,
    ready: Callable[[], None],
    stop: asyncio.Event,
) -> None:
    await publish_frames(interface, [CyclicFrame("WHEEL_SPEEDS", 0xAA, period, sample)], paused, ready, stop)


@dataclass(frozen=True)
class CyclicFrame:
    name: str
    can_id: int
    period: float
    sample: Callable[[], bytes]


async def publish_frames(
    interface: str,
    frames: list[CyclicFrame],
    paused: Callable[[], bool] | None,
    ready: Callable[[], None],
    stop: asyncio.Event,
) -> None:
    loop = asyncio.get_running_loop()
    try:
        sock = open_broadcast(interface)
    except OSError as error:
        raise TransportIOError(f"opening CAN publisher on {interface}: {error}") from error
    with closing(sock):
        first = True
        deadlines = [loop.time()] * len(frames)
        while not stop.is_set():
            now = loop.time()
            for index, frame in enumerate(frames):
                if deadlines[index] > now:
                    continue
                if paused is None or not paused():
                    payload = frame.sample()
                    try:
                        sent = sock.send(FRAME.pack(frame.can_id, len(payload), payload))
                    except OSError as error:
                        raise TransportIOError(f"sending {frame.name} on {interface}: {error}") from error
                    if sent != FRAME.size:
                        raise TransportIOError(f"short {frame.name} frame write on {interface}: {sent} bytes")
                deadlines[index] = next_deadline(deadlines[index], loop.time(), frame.period)
            if first:
                ready()
                first = False
            try:
                await asyncio.wait_for(stop.wait(), min(deadlines) - loop.time())
            except TimeoutError:
                pass
