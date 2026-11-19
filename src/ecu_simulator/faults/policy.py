"""Fault rules that are deterministic functions of a request occurrence.

The policy owns counters and response mutation. It owns no clock, event loop, socket, or
vehicle state. Delays are descriptions on ``DiagnosticResponse`` records; the transport
schedules them without blocking this synchronous pipeline.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ecu_simulator.protocols.base import negative_response
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


@dataclass(frozen=True, slots=True)
class Drop:
    """Process the request, then discard its response."""


@dataclass(frozen=True, slots=True)
class Delay:
    seconds: float

    def __post_init__(self) -> None:
        if self.seconds < 0:
            raise ValueError("delay must be non-negative")


@dataclass(frozen=True, slots=True)
class NrcOverride:
    nrc: int

    def __post_init__(self) -> None:
        _byte("nrc", self.nrc)


@dataclass(frozen=True, slots=True)
class ResponsePending:
    final_delay: float

    def __post_init__(self) -> None:
        if self.final_delay <= 0:
            raise ValueError("response-pending final_delay must be positive")


@dataclass(frozen=True, slots=True)
class Truncate:
    remove: int

    def __post_init__(self) -> None:
        if not isinstance(self.remove, int) or self.remove <= 0:
            raise ValueError("truncate remove must be a positive integer")


@dataclass(frozen=True, slots=True)
class Corrupt:
    offset: int
    xor: int

    def __post_init__(self) -> None:
        if not isinstance(self.offset, int) or self.offset < 0:
            raise ValueError("corrupt offset must be a non-negative integer")
        _byte("corrupt xor", self.xor)
        if self.xor == 0:
            raise ValueError("corrupt xor must be non-zero")


@dataclass(frozen=True, slots=True)
class Offline:
    """Bypass request handling entirely and send nothing."""


type FaultAction = Drop | Delay | NrcOverride | ResponsePending | Truncate | Corrupt | Offline


@dataclass(frozen=True, slots=True)
class FaultRule:
    action: FaultAction
    sid: int | None = None
    functional: bool | None = None
    occurrences: frozenset[int] | None = field(default_factory=lambda: frozenset({1}))

    def __post_init__(self) -> None:
        if self.sid is not None:
            _byte("sid", self.sid)
        if self.functional is not None and not isinstance(self.functional, bool):
            raise TypeError("functional must be bool or None")
        if self.occurrences is not None:
            if not isinstance(self.occurrences, frozenset):
                raise TypeError("occurrences must be a frozenset or None")
            if not self.occurrences or any(not isinstance(n, int) or n <= 0 for n in self.occurrences):
                raise ValueError("occurrences must contain unique positive integers")

    def matches(self, request: DiagnosticRequest) -> bool:
        return (self.sid is None or (bool(request.payload) and request.payload[0] == self.sid)) and (
            self.functional is None or request.functional is self.functional
        )

    def triggers(self, occurrence: int) -> bool:
        return self.occurrences is None or occurrence in self.occurrences


type ResponseHandler = Callable[[], DiagnosticResponse | None]


class FaultPolicy:
    """Apply the first triggered rule while counting every matching rule independently."""

    def __init__(self, rules: Iterable[FaultRule] = ()) -> None:
        self._rules = tuple(rules)
        self._matches = [0] * len(self._rules)

    @property
    def rules(self) -> tuple[FaultRule, ...]:
        return self._rules

    def apply(self, request: DiagnosticRequest, ordinary: ResponseHandler) -> DiagnosticResponse | None:
        triggered: FaultRule | None = None
        for index, rule in enumerate(self._rules):
            if not rule.matches(request):
                continue
            self._matches[index] += 1
            if triggered is None and rule.triggers(self._matches[index]):
                triggered = rule

        if triggered is None:
            return ordinary()
        action = triggered.action
        if isinstance(action, Offline):
            return None

        response = ordinary()
        if isinstance(action, Drop):
            return None
        if isinstance(action, NrcOverride):
            if not request.payload:
                return response
            return DiagnosticResponse(negative_response(request.payload[0], action.nrc))
        if response is None:
            return None
        if isinstance(action, Delay):
            return dataclasses.replace(response, delay=action.seconds)
        if isinstance(action, ResponsePending):
            final = dataclasses.replace(response, delay=action.final_delay)
            return DiagnosticResponse(negative_response(request.payload[0], 0x78), follow_ups=(final,))
        if isinstance(action, Truncate):
            keep = max(1, len(response.payload) - action.remove)
            return dataclasses.replace(response, payload=response.payload[:keep])
        if isinstance(action, Corrupt):
            payload = bytearray(response.payload)
            index = action.offset % len(payload)
            payload[index] ^= action.xor
            return dataclasses.replace(response, payload=bytes(payload))
        raise AssertionError(f"unhandled fault action {action!r}")


def _byte(name: str, value: int) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be a byte value 0..255, got {value!r}")
