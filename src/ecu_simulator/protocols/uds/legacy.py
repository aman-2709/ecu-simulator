"""The legacy UDS service layer as a registered protocol. Byte-identical to Phase 2.

Responses are delegated to the frozen ``uds`` modules through the thin handler; this
class only declares the SIDs of the legacy service table (0x10, 0x11, 0x19). It does not
read the ECU's DID or DTC registries yet: Phase 6 replaces it.
"""

from __future__ import annotations

from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.uds import handler as uds_handler
from ecu_simulator.uds import services as uds_services


def _legacy_sids() -> frozenset[int]:
    sids: set[int] = set()
    for service in uds_services.SERVICES:
        sid = service["id"]
        assert isinstance(sid, int)
        sids.add(sid)
    return frozenset(sids)


LEGACY_UDS_SIDS = _legacy_sids()


class LegacyUdsProtocol:
    name = "uds"
    service_ids = LEGACY_UDS_SIDS

    def handle(self, request: ServiceRequest) -> bytes | None:
        return uds_handler.handle(request.payload)
