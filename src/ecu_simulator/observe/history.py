"""The last N published exchange events, bounded by count and by encoded bytes (0010 §4.3, §4.5)."""

from __future__ import annotations

from collections import deque

from ecu_simulator.observe.limits import EXCHANGES_MAX_LIMIT, HISTORY_MAX_BYTES, HISTORY_MAX_EVENTS


def _check_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    return value


class HistoryRing:
    def __init__(self, max_events: int = HISTORY_MAX_EVENTS, max_bytes: int = HISTORY_MAX_BYTES) -> None:
        self._events: deque[tuple[int, str]] = deque()
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._bytes = 0
        self.last_seq = 0

    @property
    def oldest_seq(self) -> int | None:
        return self._events[0][0] if self._events else None

    def add(self, seq: int, text: str) -> None:
        self._events.append((seq, text))
        self._bytes += len(text.encode())
        self.last_seq = seq
        while len(self._events) > self._max_events or (self._bytes > self._max_bytes and len(self._events) > 1):
            _, old = self._events.popleft()
            self._bytes -= len(old.encode())

    def since(self, after: int | None = None, limit: int | None = None) -> tuple[list[str], bool]:
        """Events with ``seq > after`` (or the most recent), oldest first. Rules: the table in Task 4."""
        limit = EXCHANGES_MAX_LIMIT if limit is None else min(_check_int("limit", limit), EXCHANGES_MAX_LIMIT)
        if limit < 1:
            raise ValueError(f"limit must be at least 1, not {limit}")
        if after is not None and _check_int("after", after) < 0:
            raise ValueError(f"after must not be negative, not {after}")
        if after is None:
            return [text for _, text in list(self._events)[-limit:]], False
        oldest = self.oldest_seq
        gap = oldest is not None and after < oldest - 1
        return [text for seq, text in self._events if seq > after][:limit], gap

    def snapshot(self) -> list[tuple[int, str]]:
        return list(self._events)
