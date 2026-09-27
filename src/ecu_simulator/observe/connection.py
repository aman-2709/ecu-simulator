"""One WebSocket client's queue, its one-slot messages and its ledger (0010 §4.3, §5.1).

The ledger counts live ``exchange`` messages only. ``state`` and ``dropped`` are replaced,
never queued, so they can neither be dropped nor starve a client of the latest state.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

from ecu_simulator.observe.limits import CLIENT_MAX_BYTES, CLIENT_MAX_MESSAGES, CLOSE_TOO_SLOW, OVERFLOW_DISCONNECT_S


class Connection:
    def __init__(
        self,
        id: int,
        watermark: int,
        published_at_open: int,
        now: Callable[[], float] = time.monotonic,
        max_messages: int = CLIENT_MAX_MESSAGES,
        max_bytes: int = CLIENT_MAX_BYTES,
        overflow_disconnect_s: float = OVERFLOW_DISCONNECT_S,
    ) -> None:
        self.id = id
        self.watermark = watermark
        self._now = now
        self._max_messages = max_messages
        self._max_bytes = max_bytes
        self._overflow_disconnect_s = overflow_disconnect_s
        self._queue: deque[str] = deque()
        self._bytes = 0
        self._state: str | None = None
        self._dropped_notice: str | None = None
        self._overflow_since: float | None = None
        self._in_flight: str | None = None
        self.connected_at = now()
        self.closed_at: float | None = None
        self.close_code: int | None = None
        self.history_sent = 0
        self.published_at_open = published_at_open
        self._published_at_close: int | None = None
        self.offered = self.enqueued = self.client_dropped = self.sent = self.discarded_on_close = 0

    @property
    def closed(self) -> bool:
        return self.close_code is not None

    def offer(self, text: str) -> bool:
        """Accept or drop one live exchange. If this raises, it has changed nothing (amended):
        everything that can fail runs before the first counter moves, so the Publisher can
        close the connection with the ledger's identities intact.
        """
        if self.closed:
            return False
        size = len(text.encode())
        if len(self._queue) >= self._max_messages or self._bytes + size > self._max_bytes:
            now = self._now()
            if self._overflow_since is None:
                self._overflow_since = now
            self.offered += 1
            self.client_dropped += 1
            if now - self._overflow_since >= self._overflow_disconnect_s:
                self._close(CLOSE_TOO_SLOW, self.published_at_open + self.offered, now)
            return False
        self._overflow_since = None
        self._queue.append(text)
        self._bytes += size
        self.offered += 1
        self.enqueued += 1
        return True

    def set_state(self, text: str) -> None:
        if not self.closed:
            self._state = text

    def set_dropped(self, text: str) -> None:
        if not self.closed:
            self._dropped_notice = text

    def next_message(self) -> str | None:
        if self._state is not None:
            text, self._state = self._state, None
            return text
        if self._dropped_notice is not None:
            text, self._dropped_notice = self._dropped_notice, None
            return text
        if not self._queue:
            return None
        text = self._queue.popleft()
        self._bytes -= len(text.encode())
        self._in_flight = text
        return text

    def mark_sent(self) -> None:
        if self._in_flight is not None:
            self._in_flight = None
            self.sent += 1

    def close(self, code: int, published_now: int) -> None:
        if not self.closed:
            self._close(code, published_now, self._now())

    def _close(self, code: int, published_now: int, now: float) -> None:
        # No call that can fail: offer() closes through here with the clock value it already read.
        self.close_code = code
        self.closed_at = now
        self._published_at_close = published_now
        self.discarded_on_close = len(self._queue) + (1 if self._in_flight is not None else 0)
        self._queue.clear()
        self._bytes = 0
        self._in_flight = None
        self._state = self._dropped_notice = None

    def ledger(self, published_now: int) -> dict[str, Any]:
        at_close = self._published_at_close if self.closed else published_now
        return {
            "id": self.id, "connected_at": self.connected_at, "closed_at": self.closed_at,
            "close_code": self.close_code, "watermark": self.watermark, "history_sent": self.history_sent,
            "published_at_open": self.published_at_open, "published_at_close": at_close,
            "offered": self.offered, "client_dropped": self.client_dropped, "enqueued": self.enqueued,
            "sent": self.sent, "queued": len(self._queue) + (1 if self._in_flight is not None else 0),
            "discarded_on_close": self.discarded_on_close,
        }
