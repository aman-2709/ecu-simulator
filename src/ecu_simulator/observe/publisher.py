"""Drains HandOff in bounded turns, publishes to history and to every connection (0010 §4.2-4.5)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from ecu_simulator.ecu.router import AddressRouter
from ecu_simulator.observe.connection import Connection
from ecu_simulator.observe.events import classify, encode_exchange
from ecu_simulator.observe.handoff import ExchangeRecord, HandOff
from ecu_simulator.observe.history import HistoryRing
from ecu_simulator.observe.limits import (
    CLOSE_INTERNAL_ERROR,
    CLOSE_TOO_SLOW,
    CLOSED_LEDGERS_KEPT,
    MAX_CLIENTS,
    STATE_MIN_INTERVAL_S,
    TURN_MAX_RECORDS,
    TURN_MAX_S,
)
from ecu_simulator.transport.socketcan import EndpointConfig

logger = logging.getLogger(__name__)


class TooManyClients(Exception):
    """The client limit is reached. M2 turns this into HTTP 503 before the upgrade."""


class Publisher:
    def __init__(
        self,
        handoff: HandOff,
        router: AddressRouter,
        endpoints: Mapping[str, EndpointConfig],
        *,
        encode: Callable[..., str] = encode_exchange,
        monotonic: Callable[[], float] = time.monotonic,
        max_turn_records: int = TURN_MAX_RECORDS,
        max_turn_s: float = TURN_MAX_S,
        max_clients: int = MAX_CLIENTS,
        history: HistoryRing | None = None,
    ) -> None:
        self._handoff = handoff
        self._router = router
        self._endpoints = endpoints
        self._encode = encode
        self._monotonic = monotonic
        self._max_turn_records = max_turn_records
        self._max_turn_s = max_turn_s
        self._max_clients = max_clients
        self.history = history if history is not None else HistoryRing()
        self._wall_origin = (time.time(), time.monotonic_ns())
        self._event = asyncio.Event()
        self._next_id = 0
        self.connections: list[Connection] = []
        # Connection objects, not ledger copies: an exchange in flight at close may still
        # resolve as sent or discarded, and the retained ledger must show that.
        self.closed: deque[Connection] = deque(maxlen=CLOSED_LEDGERS_KEPT)
        self._last_state: str | None = None
        self.published = 0
        self.longest_turn_s = 0.0
        self.refused_clients = 0
        self.forced_disconnects = 0
        self.encode_failed = 0
        self.fanout_failed = 0
        self._logged: set[tuple[str, str]] = set()

    def wake(self) -> None:
        self._event.set()

    def drain_turn(self) -> int:
        start = self._monotonic()
        done = 0
        while len(self._handoff) and done < self._max_turn_records:
            self._publish(self._handoff.popleft())
            done += 1
            if self._monotonic() - start >= self._max_turn_s:
                break
        self.longest_turn_s = max(self.longest_turn_s, self._monotonic() - start)
        return done

    def _publish(self, record: ExchangeRecord) -> None:
        # Never raises (amended): a failed encoding becomes a fallback event, a failed offer
        # closes that one connection. Either way seq stays contiguous and the ledgers balance.
        try:
            text = self._encode(record, self._router, self._endpoints, self._wall_origin)
        except Exception as error:
            self.encode_failed += 1
            self._log_once("encode", error)
            text = self._fallback(record)
        self.published += 1
        self.history.add(record.seq, text)
        for conn in list(self.connections):
            try:
                conn.offer(text)
            except Exception as error:
                self.fanout_failed += 1
                self._log_once("fan-out", error)
                self._abandon(conn)
                continue
            if conn.closed:
                self._retire(conn)

    def _fallback(self, record: ExchangeRecord) -> str:
        try:
            outcome = classify(record, self._router)[0]
        except Exception:
            if record.error is not None:
                outcome = "error"
            else:
                outcome = "responded" if record.response is not None else "no_response"
        event = {"type": "exchange", "seq": record.seq, "outcome": outcome, "error": "encode_failed"}
        return json.dumps(event, separators=(",", ":"))

    def _abandon(self, conn: Connection) -> None:
        # The publication whose offer raised was never offered: Connection.offer changes
        # nothing when it raises (Task 5), so the connection's ledger ends one before it.
        try:
            conn.close(CLOSE_INTERNAL_ERROR, published_now=self.published - 1)
        except Exception as error:
            self._log_once("close", error)
        self._retire(conn)

    def _log_once(self, stage: str, error: Exception) -> None:
        key = (stage, type(error).__name__)
        if key not in self._logged:
            self._logged.add(key)
            logger.error("observer %s failed with %s; logged once per type, counted in /status", stage, key[1],
                         exc_info=error)

    async def run(self) -> None:
        while True:
            await self._event.wait()
            self._event.clear()
            while len(self._handoff):
                self.drain_turn()
                await asyncio.sleep(0)   # 0010 §4.2: yield after every bounded turn

    def connect(self, after: int | None = None) -> tuple[Connection, dict[str, Any], list[str]]:
        # One synchronous step, no await: nothing can be published between these lines (0010 §4.5).
        self._retire_closed()
        if len(self.connections) >= self._max_clients:
            self.refused_clients += 1
            raise TooManyClients
        watermark = self.history.last_seq
        texts, gap = self.history.since(after)       # raises on a bad after=, before anything is registered
        self._next_id += 1
        conn = Connection(self._next_id, watermark, self.published, now=self._monotonic)
        conn.history_sent = len(texts)
        if self._last_state is not None:
            conn.set_state(self._last_state)       # a new client gets the current state, changed or not
        self.connections.append(conn)
        hello: dict[str, Any] = {
            "type": "hello", "api": 1, "watermark": watermark, "oldest_seq": self.history.oldest_seq,
        }
        if after is not None:
            hello["gap"] = gap
        return conn, hello, texts

    def disconnect(self, conn: Connection, code: int) -> None:
        conn.close(code, published_now=self.published)
        self._retire(conn)

    def _retire(self, conn: Connection) -> None:
        if conn in self.connections:
            self.connections.remove(conn)
            if conn.close_code == CLOSE_TOO_SLOW:
                self.forced_disconnects += 1
            self.closed.append(conn)

    def _retire_closed(self) -> None:
        # A connection closed outside the Publisher (M2's writer task) must not hold a client
        # slot or appear as open while no exchange arrives to retire it.
        for conn in [c for c in self.connections if c.closed]:
            self._retire(conn)

    def stats(self, issued: int) -> dict[str, Any]:
        self._retire_closed()
        return {
            "clients": len(self.connections), "issued_seq": issued, "published": self.published,
            "last_published_seq": self.history.last_seq, "oldest_seq": self.history.oldest_seq,
            "handoff_dropped": self._handoff.dropped, "refused_clients": self.refused_clients,
            "forced_disconnects": self.forced_disconnects, "longest_turn_s": self.longest_turn_s,
            "encode_failed": self.encode_failed, "fanout_failed": self.fanout_failed,
            "connections": [c.ledger(self.published) for c in self.connections],
            "closed_connections": [c.ledger(self.published) for c in self.closed],
        }

    def push_state(self, text: str) -> None:
        self._last_state = text
        for conn in self.connections:
            conn.set_state(text)

    def push_dropped(self) -> None:
        for conn in self.connections:
            conn.set_dropped(json.dumps({"type": "dropped", "handoff_dropped": self._handoff.dropped,
                                         "client_dropped": conn.client_dropped,
                                         "forced_disconnects": self.forced_disconnects}, separators=(",", ":")))

    async def run_state(self, snapshot: Callable[[], str], interval_s: float = STATE_MIN_INTERVAL_S) -> None:
        """Push ``state`` at most every ``interval_s``, and only when it changed (0010 §4.3)."""
        while True:
            text = snapshot()
            if text != self._last_state:
                self.push_state(text)
            self.push_dropped()
            await asyncio.sleep(interval_s)
