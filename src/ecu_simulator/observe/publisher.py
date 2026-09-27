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

TOTAL_FIELDS = (
    "history_sent", "offered", "client_dropped", "enqueued", "sent", "delivery_unknown", "discarded_on_close",
)


# 0010 P5(h): a connection closed 1013 (forced) or 1006 (reset, vanished) may carry one
# delivery_unknown; every other connection none.
UNKNOWN_ALLOWANCE = {CLOSE_TOO_SLOW: 1, 1006: 1}


def _empty_totals() -> dict[str, Any]:
    return {"connections": 0, "published_span": 0, **dict.fromkeys(TOTAL_FIELDS, 0), "close_codes": {},
            "delivery_unknown_by_close_code": {}, "delivery_unknown_over_allowance": 0}


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
        connection_options: Mapping[str, Any] | None = None,
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
        self.writer_failed = 0
        self._logged: set[tuple[str, str]] = set()
        self._connection_options = dict(connection_options or {})
        self.connections_opened = 0
        self._unresolved: list[Connection] = []
        self._totals = _empty_totals()

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
        conn = Connection(self._next_id, watermark, self.published, now=self._monotonic, **self._connection_options)
        conn.history_sent = len(texts)
        if self._last_state is not None:
            conn.set_state(self._last_state)       # a new client gets the current state, changed or not
        self.connections.append(conn)
        self.connections_opened += 1
        hello: dict[str, Any] = {
            "type": "hello", "api": 1, "watermark": watermark, "oldest_seq": self.history.oldest_seq,
        }
        if after is not None:
            hello["gap"] = gap
        return conn, hello, texts

    def fail_writer(self, conn: Connection) -> None:
        """A connection's writer died on a healthy socket: a server fault, closed with 1011."""
        self.writer_failed += 1
        self.disconnect(conn, CLOSE_INTERNAL_ERROR)

    def disconnect(self, conn: Connection, code: int) -> None:
        conn.close(code, published_now=self.published)
        self._retire(conn)

    def _retire(self, conn: Connection) -> None:
        if conn in self.connections:
            self.connections.remove(conn)
            if conn.close_code == CLOSE_TOO_SLOW:
                self.forced_disconnects += 1
            self.closed.append(conn)
            self._unresolved.append(conn)
            self._fold()

    def _retire_closed(self) -> None:
        # A connection closed outside the Publisher (M2's writer task) must not hold a client
        # slot or appear as open while no exchange arrives to retire it.
        for conn in [c for c in self.connections if c.closed]:
            self._retire(conn)

    def _fold(self) -> None:
        # 0010 §5.1, cumulative: every closed connection, once, when its in-flight send has resolved.
        waiting = []
        for conn in self._unresolved:
            ledger = conn.ledger(self.published)
            if ledger["queued"]:
                waiting.append(conn)
                continue
            totals = self._totals
            totals["connections"] += 1
            totals["published_span"] += ledger["published_at_close"] - ledger["published_at_open"]
            for field in TOTAL_FIELDS:
                totals[field] += ledger[field]
            code = str(ledger["close_code"])
            totals["close_codes"][code] = totals["close_codes"].get(code, 0) + 1
            if ledger["delivery_unknown"]:        # 0010 P5(h): the allowance depends on the close code
                by_code = totals["delivery_unknown_by_close_code"]
                by_code[code] = by_code.get(code, 0) + ledger["delivery_unknown"]
            # Per connection, as it is added: the per-code sums cannot show one connection over
            # its allowance once its ledger is evicted (0010 §5.1).
            if ledger["delivery_unknown"] > UNKNOWN_ALLOWANCE.get(ledger["close_code"], 0):
                totals["delivery_unknown_over_allowance"] += 1
        self._unresolved = waiting

    def stats(self, issued: int) -> dict[str, Any]:
        self._retire_closed()
        self._fold()
        return {
            "clients": len(self.connections), "issued_seq": issued, "published": self.published,
            "last_published_seq": self.history.last_seq, "oldest_seq": self.history.oldest_seq,
            "handoff_dropped": self._handoff.dropped, "refused_clients": self.refused_clients,
            "forced_disconnects": self.forced_disconnects, "longest_turn_s": self.longest_turn_s,
            "encode_failed": self.encode_failed, "fanout_failed": self.fanout_failed,
            "writer_failed": self.writer_failed,
            "connections": [c.ledger(self.published) for c in self.connections],
            "closed_connections": [c.ledger(self.published) for c in self.closed],
            "connections_opened": self.connections_opened,
            "closed_totals": {**self._totals, "close_codes": dict(self._totals["close_codes"]),
                              "delivery_unknown_by_close_code": dict(self._totals["delivery_unknown_by_close_code"])},
            "closed_unresolved": len(self._unresolved),
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
