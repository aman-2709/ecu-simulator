"""The legacy OBD-II service layer as a registered protocol. Byte-identical to Phase 2.

Request parsing and responses are delegated to the frozen ``obd`` modules through the
thin handler; this class only declares which SIDs the legacy layer accepts.
"""

from __future__ import annotations

from ecu_simulator.obd import handler as obd_handler
from ecu_simulator.obd import services as obd_services
from ecu_simulator.protocols.base import ServiceRequest

# Everything obd.services.is_sid_valid accepts (0x01..0x0A), including the modes it
# answers with silence (DEV-11): claiming them keeps the ECU's unsupported-service
# policy from touching OBD behavior.
LEGACY_OBD_SIDS = frozenset(sid for sid in range(0x100) if obd_services.is_sid_valid(sid))


class LegacyObdProtocol:
    name = "obd"
    service_ids = LEGACY_OBD_SIDS

    def handle(self, request: ServiceRequest) -> bytes | None:
        return obd_handler.handle(request.payload)
