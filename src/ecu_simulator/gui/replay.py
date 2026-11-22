"""Timed replay of one Classical CAN recording on the local virtual bus."""

import asyncio
import io
import re
import socket
import struct
import time
import uuid
from dataclasses import dataclass

from ecu_simulator.gui.dbc_workflow import virtual_can
from ecu_simulator.gui.manager import RunConflict

_FRAME = re.compile(
    rb"\((\d{1,12})(?:\.(\d{1,9}))?\)\s+(\S+)\s+([\da-fA-F]{8}|[\da-fA-F]{3})"
    rb"(?:#([\da-fA-F]{0,16}|R[0-8]?)|\s+\[([0-8])\]\s*([\da-fA-F ]*))"
)
MAX_FRAMES = 300_000


@dataclass
class Recording:
    frames: list[tuple[int, bytes]]
    ids: int


def parse_recording(raw: bytes) -> Recording:
    frames: list[tuple[int, bytes]] = []
    ids = set()
    source = b""
    first_ns = 0
    for line_no, line in enumerate(io.BytesIO(raw), 1):
        line = line.strip()
        if not line:
            continue
        match = _FRAME.fullmatch(line)
        if not match:
            raise ValueError(f"Line {line_no}: expected a timestamped Classical CAN candump frame")
        seconds, fraction, interface, identifier, compact, dlc, spaced = match.groups()
        stamp = int(seconds) * 10**9 + int((fraction or b"").ljust(9, b"0"))
        if not frames:
            first_ns, source = stamp, interface
        if interface != source:
            raise ValueError(f"Line {line_no}: select a recording from a single CAN interface")
        offset = stamp - first_ns
        if frames and offset < frames[-1][0]:
            raise ValueError(f"Line {line_no}: timestamps must be in chronological order")
        can_id = int(identifier, 16)
        extended = len(identifier) == 8
        if can_id > (socket.CAN_EFF_MASK if extended else socket.CAN_SFF_MASK):
            raise ValueError(f"Line {line_no}: invalid Classical CAN identifier")
        if extended:
            can_id |= socket.CAN_EFF_FLAG
        ids.add(can_id)
        if compact and compact.startswith(b"R"):
            can_id |= socket.CAN_RTR_FLAG
            payload, size = b"", int(compact[1:] or b"0")
        else:
            try:
                payload = bytes.fromhex((compact if compact is not None else spaced).decode("ascii"))
            except ValueError as exc:
                raise ValueError(f"Line {line_no}: invalid hex payload") from exc
            size = len(payload)
            if size > 8 or (dlc and int(dlc) != size):
                raise ValueError(f"Line {line_no}: payload length does not match DLC")
        frames.append((offset, struct.pack("=IB3x8s", can_id, size, payload)))
        if len(frames) > MAX_FRAMES:
            raise ValueError(f"Select a recording with at most {MAX_FRAMES} frames")
    if not frames:
        raise ValueError("The recording contains no frames")
    return Recording(frames, len(ids))


class Replay:
    def __init__(self) -> None:
        self.loop_boundary_us = 0
        self.task: asyncio.Task | None = None
        self.info: dict = {"state": "Idle"}

    def start(self, recording: Recording, name: str, repeat: bool) -> dict:
        if self.task and not self.task.done():
            raise RunConflict("A recording is already playing")
        if not virtual_can("vcan0"):
            raise ValueError("Replay requires a virtual SocketCAN interface named vcan0")
        sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        try:
            sock.bind(("vcan0",))
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        self.loop_boundary_us = 0
        self.info = {"id": uuid.uuid4().hex, "state": "Playing", "name": name, "sent": 0,
                     "frames": len(recording.frames), "ids": recording.ids,
                     "duration": recording.frames[-1][0] / 1e9, "lap": 1}
        self.task = asyncio.create_task(self.play(recording, repeat, sock))
        # Also closes if the task is cancelled before entering its coroutine.
        self.task.add_done_callback(lambda _: sock.close())
        return self.info

    async def play(self, recording: Recording, repeat: bool, sock: socket.socket) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                start = loop.time()
                for position, (offset, frame) in enumerate(recording.frames, 1):
                    delay = start + offset / 1e9 - loop.time()
                    if delay > 0 or position % 64 == 0:
                        await asyncio.sleep(max(0, delay))
                    await loop.sock_sendall(sock, frame)
                    self.info["sent"] = position
                if not repeat:
                    self.info["state"] = "Finished"
                    return
                await asyncio.sleep(.1)
                self.loop_boundary_us = time.time_ns() // 1000
                self.info["lap"] += 1
        except OSError as exc:
            self.info.update(state="Failed", error=str(exc))

    async def stop(self, run_id: str) -> dict:
        if run_id != self.info.get("id"):
            raise RunConflict("This replay has been replaced")
        task = self.task
        if task and not task.done():
            self.info["state"] = "Stopped"
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.info
