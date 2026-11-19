"""One WebSocket client's queue, its one-slot messages and its ledger (0010 §4.3, §5.1).

The ledger counts live ``exchange`` messages only. ``state`` and ``dropped`` are replaced,
never queued, so they can neither be dropped nor starve a client of the latest state.

The writer contract (for M2's per-connection writer task)
----------------------------------------------------------
The Publisher fills a connection; exactly one writer task drains it. The writer:

0. sends ``hello``, then ``take_state()`` if not ``None``, then the history, then loops;
1. calls ``next_message()``. It returns the pending ``state``, else the pending
   ``dropped`` notice, else the oldest queued ``exchange``, else ``None`` (wait for more,
   via ``wait_changed()``);
2. awaits ``send_str(text)`` on the WebSocket;
3. then, **before calling ``next_message()`` again**, resolves that send with one of three
   outcomes: ``mark_sent()`` if ``send_str`` returned; ``mark_failed()`` only if
   non-delivery is **known**, meaning ``send_str`` raised ``NotDelivered``; and
   ``mark_unknown()`` for any other exception, or a cancellation while under way. All three
   are no-ops after a ``state`` or ``dropped`` message, which the ledger does not count, so
   the writer may call them after every message.

Only one ``exchange`` is in flight at a time. Calling ``next_message()`` again before
resolving the previous exchange loses it from the ledger, and
``enqueued = sent + delivery_unknown + queued + discarded_on_close`` then no longer holds.

**Close during an in-flight send.** ``close()`` may run while ``send_str`` is awaiting:
from the Publisher (a forced 1013), from ``Publisher.disconnect``, or from the writer
itself. Close discards everything still queued (``discarded_on_close``) and clears the
``state`` and ``dropped`` slots, but it leaves the in-flight exchange unresolved and
counted in ``queued``. The writer still resolves it when ``send_str`` finishes, by
owner decision 8: ``mark_sent()`` if it returned, ``mark_failed()`` only if it raised
``NotDelivered`` (known: nothing written), and ``mark_unknown()`` for any other exception
or a cancellation. That keeps P5(d), "received ≤ ``sent`` + ``delivery_unknown``"
(0010 §9.2), true for a frame that reached the client after a forced close. After
close, ``next_message()`` returns ``None``, and the ledger is final once the in-flight
send has resolved. A writer that stops without resolving leaves
``queued`` at 1 for good, which P5's quiesce check reports.
"""

from __future__ import annotations

import asyncio
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
        self._changed = asyncio.Event()       # a message may be available, or the connection closed
        self._closed_event = asyncio.Event()
        self.connected_at = now()
        self.closed_at: float | None = None
        self.close_code: int | None = None
        self.history_sent = 0
        self.published_at_open = published_at_open
        self._published_at_close: int | None = None
        self.offered = self.enqueued = self.client_dropped = self.sent = self.discarded_on_close = 0
        self.delivery_unknown = 0

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
        self._changed.set()
        return True

    def set_state(self, text: str) -> None:
        if not self.closed:
            self._state = text
            self._changed.set()

    def set_dropped(self, text: str) -> None:
        if not self.closed:
            self._dropped_notice = text
            self._changed.set()

    def take_state(self) -> str | None:
        """The pending ``state``, for the writer to send between ``hello`` and history (0010 §4.5)."""
        text, self._state = self._state, None
        return text

    async def wait_changed(self) -> None:
        """Until something may be sendable or the connection closed. Spurious wake-ups are harmless."""
        await self._changed.wait()
        self._changed.clear()

    async def wait_closed(self) -> None:
        await self._closed_event.wait()

    def next_message(self) -> str | None:
        """The next message to send; resolve an ``exchange`` before asking again (module docstring)."""
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
        """The writer's ``send_str`` succeeded. Valid after close too: a send already under
        way when the connection closed may still deliver, and then it counts as sent.
        """
        if self._in_flight is not None:
            self._in_flight = None
            self.sent += 1

    def mark_failed(self) -> None:
        """Known non-delivery: ``send`` raised ``NotDelivered``, so nothing was written (owner decision 8)."""
        if self._in_flight is not None:
            self._in_flight = None
            self.discarded_on_close += 1

    def mark_unknown(self) -> None:
        """Delivery unknown: ``send`` raised any exception other than ``NotDelivered``, or was
        cancelled while under way (owner decision 8); the frame may have been written.

        Neither sent nor discarded. P5 treats any such exchange as inconclusive (0010 §9.2).
        """
        if self._in_flight is not None:
            self._in_flight = None
            self.delivery_unknown += 1

    def close(self, code: int, published_now: int) -> None:
        if self.closed:
            return
        try:
            now: float | None = self._now()
        except Exception:
            now = None          # still close: a missing timestamp must not leave the ledger open
        self._close(code, published_now, now)

    def _close(self, code: int, published_now: int, now: float | None) -> None:
        # No call that can fail: offer() closes through here with the clock value it already read.
        # An exchange in flight is left to mark_sent / mark_failed, so that it is counted as
        # what actually happened to it (0010 §9.2 P5(d)); until then it counts as queued.
        self.close_code = code
        self.closed_at = now
        self._published_at_close = published_now
        self.discarded_on_close += len(self._queue)
        self._queue.clear()
        self._bytes = 0
        self._state = self._dropped_notice = None
        self._changed.set()
        self._closed_event.set()

    def ledger(self, published_now: int) -> dict[str, Any]:
        at_close = self._published_at_close if self.closed else published_now
        return {
            "id": self.id, "connected_at": self.connected_at, "closed_at": self.closed_at,
            "close_code": self.close_code, "watermark": self.watermark, "history_sent": self.history_sent,
            "published_at_open": self.published_at_open, "published_at_close": at_close,
            "offered": self.offered, "client_dropped": self.client_dropped, "enqueued": self.enqueued,
            "sent": self.sent, "queued": len(self._queue) + (1 if self._in_flight is not None else 0),
            "discarded_on_close": self.discarded_on_close, "delivery_unknown": self.delivery_unknown,
        }
