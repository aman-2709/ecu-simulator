"""One simulated ECU: a name and the protocols registered for its service identifiers.

Dispatch is by SID only. Every SID belongs to at most one protocol; a second protocol
claiming a registered SID is a configuration error (no first-match). The ECU receives
addressing-only requests and returns payload-only responses: it never sees a socket.
"""

from __future__ import annotations

import logging
from types import MappingProxyType

from ecu_simulator.logging import log_context
from ecu_simulator.protocols.base import DiagnosticProtocol, ServiceRequest
from ecu_simulator.protocols.uds.providers import DidRegistry, DtcRegistry
from ecu_simulator.transport.messages import DiagnosticRequest, DiagnosticResponse

logger = logging.getLogger(__name__)


class ServiceConflictError(ValueError):
    """A protocol name or service identifier is already registered on this ECU."""


class Ecu:
    def __init__(self, name: str, *, dids: DidRegistry | None = None, dtcs: DtcRegistry | None = None) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError(f"ECU name must be a non-empty string, got {name!r}")
        self.name = name
        # Extension points for UDS data services (plan rule 6); protocols that need them
        # receive them at construction.
        self.dids = dids if dids is not None else DidRegistry()
        self.dtcs = dtcs if dtcs is not None else DtcRegistry()
        self._protocols: dict[str, DiagnosticProtocol] = {}
        self._by_sid: dict[int, DiagnosticProtocol] = {}

    def __repr__(self) -> str:
        return f"Ecu({self.name!r}, protocols={list(self._protocols)})"

    # -- registration ------------------------------------------------------------------------

    def register(self, protocol: DiagnosticProtocol) -> None:
        """Claim ``protocol.service_ids`` for ``protocol``; all or nothing."""
        sids = frozenset(protocol.service_ids)
        if not sids:
            raise ValueError(f"{self.name}: protocol {protocol.name!r} declares no service identifiers")
        for sid in sorted(sids):
            if not isinstance(sid, int) or not 0 <= sid <= 0xFF:
                raise ValueError(f"{self.name}: protocol {protocol.name!r} declares invalid SID 0x{sid:X}")
        if protocol.name in self._protocols:
            raise ServiceConflictError(f"{self.name}: a protocol named {protocol.name!r} is already registered")
        for sid in sorted(sids):
            owner = self._by_sid.get(sid)
            if owner is not None:
                raise ServiceConflictError(
                    f"{self.name}: SID 0x{sid:02X} is claimed by both {owner.name!r} and {protocol.name!r}"
                )
        self._protocols[protocol.name] = protocol
        for sid in sids:
            self._by_sid[sid] = protocol
        logger.debug("%s: registered %s for SIDs %s", self.name, protocol.name, [f"0x{s:02X}" for s in sorted(sids)])

    @property
    def protocols(self) -> tuple[DiagnosticProtocol, ...]:
        return tuple(self._protocols.values())

    @property
    def service_ids(self) -> MappingProxyType[int, str]:
        """SID -> name of the protocol serving it."""
        return MappingProxyType({sid: protocol.name for sid, protocol in self._by_sid.items()})

    def protocol_for(self, sid: int) -> DiagnosticProtocol | None:
        return self._by_sid.get(sid)

    # -- handling ----------------------------------------------------------------------------

    def handle(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        """Answer one request; ``None`` means "send nothing"."""
        with log_context(self.name):
            return self._handle(request)

    def _handle(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        if not request.payload:
            logger.warning("%s: empty request on 0x%X ignored", self.name, request.target_address)
            return None
        service = ServiceRequest(request.payload, functional=request.functional)
        logger.info(
            "%s rx 0x%X %s request %s",
            self.name,
            request.target_address,
            "functional" if request.functional else "physical",
            service.payload.hex(),
        )
        protocol = self._by_sid.get(service.sid)
        if protocol is None:
            payload = self._unsupported_service(service)
        else:
            with log_context(self.name, protocol.name):
                payload = protocol.handle(service)
        if payload is None:
            logger.info("%s: no response", self.name)
            return None
        logger.info("%s tx response %s", self.name, payload.hex())
        return DiagnosticResponse(payload)

    def _unsupported_service(self, request: ServiceRequest) -> bytes | None:
        """No protocol claims the SID. Today: silence (DEV-06)."""
        logger.warning("%s: SID 0x%02X is not served by any protocol", self.name, request.sid)
        return None
