"""Run-scoped Classical CAN publishers on a monotonic schedule."""

import asyncio
import math
import socket
import struct
from collections.abc import Callable, Sequence
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


def first_offsets(periods: Sequence[float]) -> list[float]:
    """Each frame's first deadline after the start, spreading the frames of one period evenly across that period.

    The k-th of n frames with the same period starts k/n of a period late, in list order, so a whole car does not hit
    the interface's transmit queue as one burst at the first tick, and its frames stay spread afterwards because each
    keeps its period. Every offset is below its frame's period, so every frame is sent within one period of the start.
    """
    groups: dict[float, list[int]] = {}
    for index, period in enumerate(periods):
        groups.setdefault(period, []).append(index)
    offsets = [0.0] * len(periods)
    for period, members in groups.items():
        for k, index in enumerate(members):
            offsets[index] = period * k / len(members)
    return offsets


async def publish_wheels(
    interface: str,
    period: float,
    sample: Callable[[], bytes],
    paused: Callable[[], bool] | None,
    ready: Callable[[], None],
    stop: asyncio.Event,
) -> None:
    await publish_frames(interface, [CyclicFrame("WHEEL_SPEEDS", 0xAA, period, sample)], paused, ready, stop)


def wire_id(can_id: int, extended: bool) -> int:
    """The identifier word of a raw CAN frame: a 29-bit ``can_id`` carries ``CAN_EFF_FLAG``."""
    return can_id | (socket.CAN_EFF_FLAG if extended else 0)


@dataclass(frozen=True)
class CyclicFrame:
    name: str
    can_id: int  # as wire_id gives it
    period: float
    sample: Callable[[], bytes]


async def publish_frames(
    interface: str,
    frames: list[CyclicFrame],
    paused: Callable[[], bool] | None,
    ready: Callable[[], None],
    stop: asyncio.Event,
    *,
    stagger: bool = False,
) -> None:
    """Send each frame every ``period`` on absolute deadlines until ``stop`` is set; ``ready`` follows the first tick.

    By default every frame's first deadline is the start. ``stagger`` spreads them with ``first_offsets`` instead; the
    car run uses it, because a whole car's first tick can overflow a physical interface's transmit queue.
    """
    loop = asyncio.get_running_loop()
    try:
        sock = open_broadcast(interface)
    except OSError as error:
        raise TransportIOError(f"opening CAN publisher on {interface}: {error}") from error
    with closing(sock):
        first = True
        start = loop.time()
        offsets = first_offsets([frame.period for frame in frames]) if stagger else [0.0] * len(frames)
        deadlines = [start + offset for offset in offsets]
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
