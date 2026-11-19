"""The data identifiers UDS 0x22 serves (Phase 11).

Each is a :class:`~ecu_simulator.protocols.uds.providers.DidProvider` registered on the ECU's
DID registry (plan rule 6). Two are built in and one carries the profile's own:

* **F186** ActiveDiagnosticSessionDataIdentifier: one byte, the active session. AUTOSAR has
  the Dcm manage this DID internally ([SWS_Dcm_00085]); no accessible source states its
  layout beyond "the active session", so the single session byte is a project reading.
* **F190** VIN: the vehicle's VIN as configured, unpadded (ruling Q5). OBD Mode 09 left-pads
  a short VIN to 17 bytes; that is Mode 09's own pinned behavior and not evidence for 0x22.
* **Profile DIDs**: the hex bytes the profile states, verbatim (ruling Q6).

**Nothing here is standards validated.** See
docs/decisions/0011-phase-11-uds-session-handling.md, rows W6 to W8.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ecu_simulator.protocols.uds.providers import DidWriteAccess
from ecu_simulator.protocols.uds.session import UdsSession
from ecu_simulator.vehicle import VehicleState

ACTIVE_DIAGNOSTIC_SESSION = 0xF186
VIN = 0xF190


class SessionDidProvider:
    """F186: the active diagnostic session."""

    name = "session"
    data_identifiers = frozenset({ACTIVE_DIAGNOSTIC_SESSION})

    def __init__(self, session: UdsSession) -> None:
        self.session = session

    def read(self, did: int) -> bytes | None:
        if did != ACTIVE_DIAGNOSTIC_SESSION:
            return None
        return bytes([self.session.active])


class VinDidProvider:
    """F190: the VIN, read from the vehicle on every request."""

    name = "vin"
    data_identifiers = frozenset({VIN})

    def __init__(self, vehicle: VehicleState) -> None:
        self.vehicle = vehicle

    def read(self, did: int) -> bytes | None:
        if did != VIN:
            return None
        return str(self.vehicle.get("vehicle.vin")).encode()


class StaticDidProvider:
    """Configured byte strings, optionally writable at their fixed initial length."""

    def __init__(
        self,
        name: str,
        data: Mapping[int, bytes],
        *,
        writable: Mapping[int, DidWriteAccess] | None = None,
    ) -> None:
        self._name = name
        self._data = {did: bytes(value) for did, value in data.items()}
        self._writable = MappingProxyType(dict(writable or {}))
        unowned = set(self._writable) - set(self._data)
        if unowned:
            raise ValueError(
                f"writable DIDs {[f'0x{did:04X}' for did in sorted(unowned)]} are not in its data identifiers"
            )
        for did, access in self._writable.items():
            if access.length != len(self._data[did]):
                raise ValueError(
                    f"writable DID 0x{did:04X} length {access.length} differs from its "
                    f"{len(self._data[did])}-byte value"
                )

    @property
    def name(self) -> str:
        return self._name

    @property
    def data_identifiers(self) -> frozenset[int]:
        return frozenset(self._data)

    def read(self, did: int) -> bytes | None:
        return self._data.get(did)

    @property
    def writable_data_identifiers(self) -> frozenset[int]:
        return frozenset(self._writable)

    def write_access(self, did: int) -> DidWriteAccess | None:
        return self._writable.get(did)

    def write(self, did: int, data: bytes) -> None:
        access = self._writable.get(did)
        if access is None:
            raise KeyError(f"DID 0x{did:04X} is not writable")
        if len(data) != access.length:
            raise ValueError(f"DID 0x{did:04X} requires {access.length} bytes, got {len(data)}")
        self._data[did] = bytes(data)


SIGNAL_ENCODINGS = frozenset({"boolean", "uint8", "uint16_be", "uint32_be", "int16_be", "float32_be"})


@dataclass(frozen=True, slots=True)
class SignalDid:
    """A vehicle signal and the explicit bytes used to expose it through UDS."""

    path: str
    encoding: str

    def __post_init__(self) -> None:
        if self.encoding not in SIGNAL_ENCODINGS:
            raise ValueError(f"unknown signal DID encoding {self.encoding!r}")


class SignalDidProvider:
    """Read-only DIDs encoded from the vehicle's current signal values."""

    def __init__(self, name: str, vehicle: VehicleState, definitions: Mapping[int, SignalDid]) -> None:
        self._name = name
        self._vehicle = vehicle
        self._definitions = MappingProxyType(dict(definitions))

    @property
    def name(self) -> str:
        return self._name

    @property
    def data_identifiers(self) -> frozenset[int]:
        return frozenset(self._definitions)

    def read(self, did: int) -> bytes | None:
        definition = self._definitions.get(did)
        if definition is None:
            return None
        return encode_signal(self._vehicle.get(definition.path), definition.encoding)


def encode_signal(value: object, encoding: str) -> bytes:
    """Encode one value exactly as its profile declares, rejecting implicit coercions."""
    if encoding == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"boolean encoding requires bool, got {value!r}")
        return bytes([int(value)])
    if encoding in {"uint8", "uint16_be", "uint32_be", "int16_be"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{encoding} encoding requires int, got {value!r}")
        formats = {"uint8": ">B", "uint16_be": ">H", "uint32_be": ">I", "int16_be": ">h"}
        try:
            return struct.pack(formats[encoding], value)
        except struct.error as error:
            raise ValueError(f"{value!r} does not fit {encoding}") from error
    if encoding == "float32_be":
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"float32_be encoding requires a finite number, got {value!r}")
        try:
            return struct.pack(">f", value)
        except (OverflowError, struct.error) as error:
            raise ValueError(f"{value!r} does not fit float32_be") from error
    raise ValueError(f"unknown signal DID encoding {encoding!r}")
