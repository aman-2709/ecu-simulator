"""A scripted ELM327 on a pseudo-terminal, for testing the driver without a dongle.

It reproduces the three framing behaviours a naive fake would omit, and which the parser
exists to absorb: it echoes the command (``E1`` is the device default), it terminates every
response with the ``>`` prompt, and it can emit a NUL, which ELM327DSJ page 9 warns may
appear anywhere and tells software to strip.

Pure stdlib apart from the pty itself -- no pyserial here, so the driver under test is the
only thing holding that dependency.
"""

from __future__ import annotations

import os
import pty
import threading


class FakeElm327:
    """Answers commands from a lookup table, like a very literal adapter."""

    UNKNOWN = "?\r\r>"

    def __init__(self, responses: dict[str, str], inject_nul: bool = False) -> None:
        self.responses = dict(responses)
        self.inject_nul = inject_nul
        self.received: list[str] = []
        self._master, self._slave = pty.openpty()
        # The slave fd is deliberately held open for the object's lifetime. If every slave
        # fd is closed, reading the master fails with EIO and the responder thread dies
        # before the driver ever opens the port.
        self.port = os.ttyname(self._slave)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                data = os.read(self._master, 256)
            except OSError:
                return
            if not data:
                return
            os.write(self._master, data)  # echo, as E1 does
            buffer += data
            while b"\r" in buffer:
                line, buffer = buffer.split(b"\r", 1)
                text = line.decode(errors="replace")
                self.received.append(text + "\r")
                key = text.replace(" ", "").upper()
                body = self.responses.get(key, self.UNKNOWN).encode()
                os.write(self._master, (b"\x00" if self.inject_nul else b"") + body)

    def stop(self) -> None:
        self._stop.set()
        for fd in (self._master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass
