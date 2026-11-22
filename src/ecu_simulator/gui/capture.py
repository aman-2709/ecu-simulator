"""Receive timestamped Classical CAN frames from one Linux SocketCAN interface."""

import socket
import struct
import time

from ecu_simulator.restbus import WheelCodec
from ecu_simulator.transport.socketcan.interface import check_interface

FRAME = struct.Struct("=IB3x8s")
TIMESPEC = struct.Struct("@ll")
# Linux socket options may be absent from Python's socket module.
TIMESTAMP_NS = getattr(socket, "SO_TIMESTAMPNS", 35)
RXQ_OVFL = getattr(socket, "SO_RXQ_OVFL", 40)
CAN_RAW_ERR_FILTER = 2  # linux/can/raw.h
ANCILLARY_SIZE = socket.CMSG_SPACE(TIMESPEC.size) + socket.CMSG_SPACE(4)
BATCH_LIMIT = 256


def open_capture(interface: str) -> socket.socket:
    if not interface:
        raise ValueError("Select a CAN interface")
    check_interface(interface)
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    try:
        sock.setsockopt(socket.SOL_SOCKET, TIMESTAMP_NS, 1)
        sock.setsockopt(socket.SOL_SOCKET, RXQ_OVFL, 1)
        sock.setsockopt(socket.SOL_CAN_RAW, CAN_RAW_ERR_FILTER, struct.pack("=I", socket.CAN_ERR_MASK))
        sock.bind((interface,))
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def read_batch(sock: socket.socket) -> tuple[list[dict], int | None]:
    frames = []
    dropped = None
    for _ in range(BATCH_LIMIT):
        try:
            data, metadata, _, _ = sock.recvmsg(FRAME.size, ANCILLARY_SIZE)
        except BlockingIOError:
            break
        can_id, length, payload = FRAME.unpack(data)
        timestamp = time.time_ns() // 1000
        for level, kind, value in metadata:
            if level != socket.SOL_SOCKET:
                continue
            if kind == TIMESTAMP_NS:
                seconds, nanoseconds = TIMESPEC.unpack(value)
                timestamp = seconds * 1_000_000 + nanoseconds // 1000
            elif kind == RXQ_OVFL:
                dropped, = struct.unpack("=I", value)
        error = bool(can_id & socket.CAN_ERR_FLAG)
        extended = bool(can_id & socket.CAN_EFF_FLAG)
        remote = bool(can_id & socket.CAN_RTR_FLAG)
        mask = socket.CAN_EFF_MASK if extended or error else socket.CAN_SFF_MASK
        frames.append({
            "time": timestamp, "id": f"{can_id & mask:0{8 if extended or error else 3}X}",
            "kind": "ERR" if error else "RTR" if remote else "", "length": length,
            "bytes": [] if remote else list(payload[:length]),
        })
    return frames, dropped


class WheelMonitor:
    """Decode the active run's wheel frames on a single capture connection."""

    def __init__(self, interface: str):
        self.interface = interface
        self.run_id: str | None = None
        self.codec: WheelCodec | None = None
        self.last_time: int | None = None
        self.latest: dict = {}

    def observe(self, run: dict, frames: list[dict], now_us: int) -> dict | None:
        profile = run.get("restbus")
        active = profile and run["state"] == "Running" and run["interface"] == self.interface
        run_id = run["id"] if active else None
        if run_id != self.run_id:
            self.run_id = run_id
            self.last_time = None
            self.latest = {"bytes": [], "speeds": None, "period_ms": None}
            self.codec = None
        if not active or profile is None:
            return None
        if self.codec is None:
            self.codec = WheelCodec()

        for frame in frames:
            if frame["time"] < profile["started_us"]:
                continue
            if frame["kind"] or frame["id"] != f'{profile["can_id"]:03X}':
                continue
            period = None if self.last_time is None else (frame["time"] - self.last_time) / 1000
            self.last_time = frame["time"]
            data = frame["bytes"]
            speeds = self.codec.decode(bytes(data)) if frame["length"] == len(data) == 8 else None
            frame["wheels"] = speeds
            self.latest = {"bytes": data, "period_ms": period, "speeds": speeds}
        age = None if self.last_time is None else max(0, (now_us - self.last_time) / 1000)
        if age is None:
            state = "Waiting"
        elif age >= 5 * profile["period_ms"]:
            state = "Stale"
        elif self.latest["speeds"] is None:
            state = "Malformed"
        else:
            state = "Live"
        return {**self.latest, "run_id": run_id, "age_ms": age, "state": state}
