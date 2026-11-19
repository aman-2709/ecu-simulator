"""Addressing-only request and response records exchanged with a transport.

A transport fills a :class:`DiagnosticRequest` with what it knows, the payload and
the addresses involved, and receives a :class:`DiagnosticResponse` carrying only the
bytes to send back. Neither record references an ECU, a protocol or vehicle state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AddressingMode(StrEnum):
    """ISO 15765-2 normal addressing with 11-bit or 29-bit CAN identifiers."""

    NORMAL_11BIT = "normal_11bit"
    NORMAL_FIXED_29BIT = "normal_fixed_29bit"


@dataclass(frozen=True, slots=True)
class DiagnosticRequest:
    """A diagnostic payload received by a transport.

    ``target_address`` is the address the request arrived on (for CAN, the RX CAN
    identifier). ``source_address`` is the tester's address when the transport knows
    it, which normal 11-bit addressing does not convey. ``endpoint_name`` identifies
    the receiving transport endpoint when several endpoints share an address.
    ``context`` is an opaque token the transport uses to route the reply.
    """

    payload: bytes
    target_address: int
    functional: bool = False
    addressing_mode: AddressingMode = AddressingMode.NORMAL_11BIT
    source_address: int | None = None
    endpoint_name: str | None = None
    context: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes | bytearray):
            raise TypeError("payload must be bytes")
        object.__setattr__(self, "payload", bytes(self.payload))
        if self.target_address < 0:
            raise ValueError("target_address must be non-negative")
        if self.endpoint_name is not None and not self.endpoint_name:
            raise ValueError("endpoint_name must be non-empty when present")


@dataclass(frozen=True, slots=True)
class DiagnosticResponse:
    """A diagnostic payload to transmit.

    ``delay`` is the number of seconds from handler completion before this payload is
    sent. ``follow_ups`` are additional responses on the same endpoint; each carries its
    own absolute delay from that same instant.
    """

    payload: bytes
    delay: float = 0.0
    follow_ups: tuple[DiagnosticResponse, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes | bytearray):
            raise TypeError("payload must be bytes")
        object.__setattr__(self, "payload", bytes(self.payload))
        if not self.payload:
            raise ValueError("payload must not be empty")
        if self.delay < 0:
            raise ValueError("delay must be non-negative")
        if not isinstance(self.follow_ups, tuple) or any(
            not isinstance(response, DiagnosticResponse) for response in self.follow_ups
        ):
            raise TypeError("follow_ups must be a tuple of DiagnosticResponse records")
