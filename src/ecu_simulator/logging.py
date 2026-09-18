"""Per-ECU and per-protocol logger context.

An ECU handles each request inside :func:`log_context`; a :class:`ContextFilter` on the
application's handlers stamps every record made meanwhile with ``ecu`` and ``protocol``
attributes, so lines from the legacy service modules (which know nothing about ECUs)
are attributed correctly without touching them. Outside any context both read ``-``.

The context is a :mod:`contextvars` variable: request handling is synchronous on the
event loop thread, so entering and leaving it around one ``handle`` call is exact.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
LOG_FORMAT = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - [%(ecu)s/%(protocol)s] %(message)s"
NO_CONTEXT = "-"


@dataclass(frozen=True, slots=True)
class LogContext:
    ecu: str
    protocol: str | None = None


_context: ContextVar[LogContext | None] = ContextVar("ecu_simulator_log_context", default=None)


def current_context() -> LogContext | None:
    return _context.get()


@contextmanager
def log_context(ecu: str, protocol: str | None = None) -> Iterator[LogContext]:
    """Attribute every record made inside the block to ``ecu`` (and ``protocol``)."""
    context = LogContext(ecu, protocol)
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


class ContextFilter(logging.Filter):
    """Stamp ``record.ecu`` and ``record.protocol`` from the current context; never drops."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = _context.get()
        record.ecu = context.ecu if context is not None else NO_CONTEXT
        record.protocol = context.protocol if context is not None and context.protocol else NO_CONTEXT
        return True
