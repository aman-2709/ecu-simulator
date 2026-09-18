"""Test doubles for the ISO-TP boundary.

``FakeIsotpSocket`` mimics the subset of ``isotp.socket`` that
``ecu_simulator.transport.socketcan.isotp.IsoTpSocket`` uses. It is backed by a
``socket.socketpair()`` so that it has a real file descriptor an asyncio loop can
watch: writing to ``feed`` makes it readable, ``recv`` drains it.
"""

from __future__ import annotations

import socket
from typing import Any


class FakeIsotpSocket:
    instances: list[FakeIsotpSocket] = []

    def __init__(self, *, bind_error: OSError | None = None, busy: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.bind_error = bind_error
        self.busy = busy
        self.sent: list[bytes] = []
        self.closed = False
        self.timeout: float | None = None
        self.recv_error: OSError | None = None
        self._inner, self.feed = socket.socketpair()
        self._inner.setblocking(False)
        FakeIsotpSocket.instances.append(self)

    # -- isotp.socket API ---------------------------------------------------------------
    def set_opts(self, **kwargs: Any) -> None:
        self.calls.append(("set_opts", kwargs))

    def set_fc_opts(self, **kwargs: Any) -> None:
        self.calls.append(("set_fc_opts", kwargs))

    def bind(self, interface: str, address: Any) -> None:
        self.calls.append(("bind", (interface, address)))
        if self.bind_error is not None:
            raise self.bind_error

    def settimeout(self, value: float | None) -> None:
        self.timeout = value
        self.calls.append(("settimeout", value))

    def gettimeout(self) -> float | None:
        return self.timeout

    def fileno(self) -> int:
        return self._inner.fileno()

    def recv(self) -> bytes:
        if self.recv_error is not None:
            error, self.recv_error = self.recv_error, None
            try:
                self._inner.recv(4095)  # the kernel reports the error instead of delivering data
            except BlockingIOError:
                pass
            raise error
        return self._inner.recv(4095)  # raises BlockingIOError when nothing is pending

    def send(self, data: bytes) -> int:
        if self.busy:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        self.sent.append(bytes(data))
        return len(data)

    def close(self) -> None:
        self.closed = True
        self._inner.close()
        self.feed.close()


def factory(**kwargs: Any):
    """Return a zero-argument factory producing a FakeIsotpSocket with the given settings."""
    return lambda: FakeIsotpSocket(**kwargs)
