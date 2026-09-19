"""Contract between an ECU and the diagnostic protocols it registers.

A protocol declares the service identifiers (SIDs) it serves and answers one
:class:`ServiceRequest` at a time with response bytes, or ``None`` for "send nothing".
Protocols are synchronous, hold no sockets, and never learn on which socket a request
arrived; the ECU supplies only what the protocol needs: the payload and whether the
request was functionally addressed.

The response-code framing shared by OBD-on-CAN and UDS (positive SID = SID + 0x40,
negative response ``7F <SID> <NRC>``) lives here so that the ECU can answer for
services no protocol claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

POSITIVE_RESPONSE_OFFSET = 0x40
NEGATIVE_RESPONSE_SID = 0x7F

NRC_SERVICE_NOT_SUPPORTED = 0x11
NRC_SUB_FUNCTION_NOT_SUPPORTED = 0x12
NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT = 0x13
NRC_REQUEST_OUT_OF_RANGE = 0x31


@dataclass(frozen=True, slots=True)
class ServiceRequest:
    """What a protocol sees: the payload and the addressing kind, nothing else."""

    payload: bytes
    functional: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes | bytearray):
            raise TypeError("payload must be bytes")
        if not self.payload:
            raise ValueError("payload must not be empty")
        object.__setattr__(self, "payload", bytes(self.payload))

    @property
    def sid(self) -> int:
        return self.payload[0]


@runtime_checkable
class DiagnosticProtocol(Protocol):
    """A registered protocol: a name, the SIDs it claims, and a synchronous handler."""

    @property
    def name(self) -> str: ...

    @property
    def service_ids(self) -> frozenset[int]: ...

    def handle(self, request: ServiceRequest) -> bytes | None: ...


def positive_response_sid(sid: int) -> int:
    _check_byte("sid", sid)
    return sid + POSITIVE_RESPONSE_OFFSET


def negative_response(sid: int, nrc: int) -> bytes:
    """``7F <sid> <nrc>``."""
    _check_byte("sid", sid)
    _check_byte("nrc", nrc)
    return bytes([NEGATIVE_RESPONSE_SID, sid, nrc])


def _check_byte(name: str, value: int) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be one byte, got {value!r}")
