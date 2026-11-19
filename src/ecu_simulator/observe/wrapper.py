"""The only observer code on the dispatch path (0010 §4.2): time the call, append one record.

The transport sends the reply only after this returns, so everything here is added to
request-to-reply latency. That is why this is all it does.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ecu_simulator.observe.handoff import ExchangeRecord, HandOff
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse

Handler = Callable[[DiagnosticRequest], DiagnosticResponse | None]


class ObservedDispatcher:
    def __init__(
        self,
        inner: Handler,
        handoff: HandOff,
        wake: Callable[[], None],
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.inner = inner
        self._handoff = handoff
        self._wake = wake
        self._clock_ns = clock_ns
        self.issued = 0

    def __call__(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        self.issued += 1
        seq = self.issued
        t0 = self._clock_ns()
        try:
            response = self.inner(request)
        except BaseException as error:
            self._handoff.append(ExchangeRecord(seq, t0, self._clock_ns() - t0, request, None, type(error).__name__))
            self._wake()
            raise
        self._handoff.append(ExchangeRecord(seq, t0, self._clock_ns() - t0, request, response, None))
        self._wake()
        return response
