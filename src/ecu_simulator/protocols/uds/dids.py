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

from collections.abc import Mapping
from types import MappingProxyType

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
    """DIDs whose data is fixed by configuration."""

    def __init__(self, name: str, data: Mapping[int, bytes]) -> None:
        self._name = name
        self._data = MappingProxyType({did: bytes(value) for did, value in data.items()})

    @property
    def name(self) -> str:
        return self._name

    @property
    def data_identifiers(self) -> frozenset[int]:
        return frozenset(self._data)

    def read(self, did: int) -> bytes | None:
        return self._data.get(did)
