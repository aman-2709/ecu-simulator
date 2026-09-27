"""The single bounded buffer between the dispatch hot path and everything else (0010 §4.2).

``append`` is the only thing the hot path calls. It never copies a payload and never
formats anything: the record holds references to bytes that already exist.
"""

from __future__ import annotations

from collections import deque
from typing import NamedTuple

from ecu_simulator.observe.limits import HANDOFF_MAX_BYTES, HANDOFF_MAX_RECORDS
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


class ExchangeRecord(NamedTuple):
    seq: int
    t0_ns: int
    elapsed_ns: int
    request: DiagnosticRequest
    response: DiagnosticResponse | None
    error: str | None


def _size(record: ExchangeRecord) -> int:
    return len(record.request.payload) + (len(record.response.payload) if record.response is not None else 0)


class HandOff:
    def __init__(self, max_records: int = HANDOFF_MAX_RECORDS, max_bytes: int = HANDOFF_MAX_BYTES) -> None:
        self._records: deque[ExchangeRecord] = deque()
        self._max_records = max_records
        self._max_bytes = max_bytes
        self.bytes = 0
        self.dropped = 0

    def __len__(self) -> int:
        return len(self._records)

    def append(self, record: ExchangeRecord) -> bool:
        """Keep ``record``, or drop it and count the drop. The dispatcher never sees a drop."""
        size = _size(record)
        if len(self._records) >= self._max_records or self.bytes + size > self._max_bytes:
            self.dropped += 1
            return False
        self._records.append(record)
        self.bytes += size
        return True

    def popleft(self) -> ExchangeRecord:
        record = self._records.popleft()
        self.bytes -= _size(record)
        return record
