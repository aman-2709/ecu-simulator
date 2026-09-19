"""One simulated ECU: a name and the protocols registered for its service identifiers.

Dispatch is by SID, restricted to the protocols the request's route enables. Every SID
belongs to at most one protocol; a second protocol claiming a registered SID is a
configuration error (no first-match). The ECU receives addressing-only requests and
returns payload-only responses: it never sees a socket.
"""

from __future__ import annotations

import logging
from types import MappingProxyType

from ecu_simulator.dtc import DtcStore
from ecu_simulator.ecu.router import Route
from ecu_simulator.logging import log_context
from ecu_simulator.protocols.base import (
    NRC_SERVICE_NOT_SUPPORTED,
    DiagnosticProtocol,
    ServiceRequest,
    negative_response,
)
from ecu_simulator.protocols.uds.providers import DidRegistry, DtcRegistry
from ecu_simulator.transport.messages import DiagnosticRequest, DiagnosticResponse

logger = logging.getLogger(__name__)


class ServiceConflictError(ValueError):
    """A protocol name or service identifier is already registered on this ECU."""


class Ecu:
    def __init__(
        self,
        name: str,
        *,
        dids: DidRegistry | None = None,
        dtc_providers: DtcRegistry | None = None,
        dtc_store: DtcStore | None = None,
    ) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError(f"ECU name must be a non-empty string, got {name!r}")
        self.name = name
        # Extension points for UDS data services (plan rule 6); protocols that need them
        # receive them at construction.
        self.dids = dids if dids is not None else DidRegistry()
        self.dtc_providers = dtc_providers if dtc_providers is not None else DtcRegistry()
        # Domain state, shared by every protocol on this ECU. dtc_providers above is the
        # UDS-side registry that encodes a view of it; the two are deliberately distinct,
        # and the store holds no encoded bytes (plan rule 7).
        self.dtc_store = dtc_store if dtc_store is not None else DtcStore()
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
        for sid in sids:
            if not isinstance(sid, int):
                raise ValueError(f"{self.name}: protocol {protocol.name!r} declares non-integer SID {sid!r}")
            if not 0 <= sid <= 0xFF:
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

    def handle(self, request: DiagnosticRequest, route: Route) -> DiagnosticResponse | None:
        """Answer one request as ``route`` permits; ``None`` means "send nothing".

        ``route`` names the protocols this ECU serves on the address the request arrived
        on. A protocol it does not name is never invoked, so nothing it would have
        produced has to be filtered out afterwards. A response an eligible protocol does
        produce is transmitted as-is: it is never discarded for being negative, nor
        because the request was functionally addressed.
        """
        if route.ecu != self.name:
            raise ValueError(f"{self.name}: route belongs to ECU {route.ecu!r}")
        with log_context(self.name):
            return self._handle(request, route)

    def _handle(self, request: DiagnosticRequest, route: Route) -> DiagnosticResponse | None:
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
        protocol = self._eligible_protocol(service, route)
        payload: bytes | None
        if protocol is None:
            payload = self._unserved_on_this_route(service, route)
        else:
            with log_context(self.name, protocol.name):
                payload = protocol.handle(service)
        if payload is None:
            logger.info("%s: no response", self.name)
            return None
        if not payload:
            # A protocol must return None for "send nothing"; an empty payload is a bug
            # in it, not an empty frame to transmit.
            logger.error("%s: protocol returned an empty response; nothing sent", self.name)
            return None
        logger.info("%s tx response %s", self.name, payload.hex())
        return DiagnosticResponse(payload)

    def _eligible_protocol(self, request: ServiceRequest, route: Route) -> DiagnosticProtocol | None:
        """The protocol serving this SID on this route, or ``None`` if the route has none."""
        protocol = self._by_sid.get(request.sid)
        if protocol is None:
            return None
        if not route.permits(protocol.name):
            # The SID is served by this ECU, but not on the address it arrived on. The
            # protocol is not called at all.
            logger.info(
                "%s: SID 0x%02X belongs to %s, which is not enabled on this route (enabled: %s)",
                self.name,
                request.sid,
                protocol.name,
                ", ".join(sorted(route.protocols)),
            )
            return None
        return protocol

    def _unserved_on_this_route(self, request: ServiceRequest, route: Route) -> bytes | None:
        """No eligible protocol claims the SID; the route decides whether to answer."""
        logger.warning(
            "%s: SID 0x%02X is not served by any protocol enabled here (%s)",
            self.name,
            request.sid,
            ", ".join(sorted(route.protocols)),
        )
        if not route.answer_unsupported:
            return None
        return negative_response(request.sid, NRC_SERVICE_NOT_SUPPORTED)
