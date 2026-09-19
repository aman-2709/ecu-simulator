"""OBD-II protocol built on the parameter table and the vehicle state.

Replaces the legacy service and response modules. Every value it reports comes from
:class:`~ecu_simulator.vehicle.VehicleState` through a signal path, and the only place a
physical quantity becomes bytes is a parameter encoder. Handling a request observes state
and never advances it.

Behaviours retained deliberately, because no reviewed evidence supports changing them in
this phase, each tracked by its DEV identifier:

* modes 0x01 to 0x0A are all claimed, and a mode with no implementation answers with
  silence rather than a negative response (DEV-11);
* only the first two request bytes are examined (DEV-18);
* a Mode 03 request carrying a trailing byte echoes it after the service identifier
  (DEV-15);
* Mode 09 PID 0A keeps its exact bytes, including the absent count byte and the leading
  padding (DEV-03, deferred by docs/decisions/0003-phase-5-obd-evidence.md).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ecu_simulator import dtc_utils
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.obd import masks
from ecu_simulator.protocols.obd.pids import MODE01_PIDS, PidDefinition, supported_pids
from ecu_simulator.vehicle import VehicleState

logger = logging.getLogger(__name__)

POSITIVE_RESPONSE_OFFSET = 0x40

MODE_CURRENT_DATA = 0x01
MODE_STORED_DTCS = 0x03
MODE_VEHICLE_INFO = 0x09

# Claimed so that unimplemented modes keep answering with silence (DEV-11) rather than
# falling through to the ECU's unsupported-service policy, which would be an unreviewed
# wire change.
CLAIMED_SERVICE_IDS = frozenset(range(0x01, 0x0B))

VIN_LENGTH = 17
VIN_ITEM_COUNT = 1  # DEV-02: one VIN per vehicle
ECU_NAME_LENGTH = 20
MAX_DTCS_IN_RESPONSE = 255

INFO_VIN = 0x02
INFO_ECU_NAME = 0x0A


class ObdProtocol:
    """Serves OBD-II Mode 01, Mode 03 and Mode 09 for one ECU."""

    name = "obd"
    service_ids = CLAIMED_SERVICE_IDS

    def __init__(self, vehicle: VehicleState, *, ecu_name: str, dtcs: Sequence[str] = ()) -> None:
        self.vehicle = vehicle
        self.ecu_name = ecu_name
        self.dtcs = list(dtcs)

    # -- parameter support -------------------------------------------------------------------

    @property
    def supported_mode01_pids(self) -> frozenset[int]:
        """Data parameters this vehicle can answer, excluding the range identifiers."""
        return supported_pids(self.vehicle)

    def definition(self, pid: int) -> PidDefinition | None:
        definition = MODE01_PIDS.get(pid)
        if definition is None or not definition.supported_by(self.vehicle):
            return None
        return definition

    # -- request handling --------------------------------------------------------------------

    def handle(self, request: ServiceRequest) -> bytes | None:
        payload = request.payload
        sid = payload[0]
        pid = payload[1] if len(payload) >= 2 else None  # DEV-18: later bytes are ignored
        if sid not in CLAIMED_SERVICE_IDS:
            return None
        if pid is not None and not 0 <= pid <= 0xFF:
            return None
        if sid == MODE_CURRENT_DATA:
            return self._mode01(pid)
        if sid == MODE_STORED_DTCS:
            return self._mode03(pid)
        if sid == MODE_VEHICLE_INFO:
            return self._mode09(pid)
        logger.info("OBD mode 0x%02X is not implemented; no response", sid)
        return None  # DEV-11

    # -- modes -------------------------------------------------------------------------------

    def _mode01(self, pid: int | None) -> bytes | None:
        if pid is None:
            return None
        if masks.is_range_request(pid):
            supported = self.supported_mode01_pids
            if not masks.is_advertised_range(pid, supported):
                # Not advertised, so not answered: DEV-04 keeps the two in agreement.
                logger.info("OBD range 0x%02X is not advertised by this vehicle; no response", pid)
                return None
            return self._prefix(MODE_CURRENT_DATA, pid, masks.supported_mask(pid, supported))
        definition = self.definition(pid)
        if definition is None:
            logger.info("OBD PID 0x%02X is not supported by this vehicle; no response", pid)
            return None
        return self._prefix(MODE_CURRENT_DATA, pid, definition.read(self.vehicle))

    def _mode03(self, pid: int | None) -> bytes:
        encoded = dtc_utils.encode_obd_dtcs(self.dtcs)
        count = len(encoded) // 2
        body = bytes([count]) + bytes(encoded) if 0 < count <= MAX_DTCS_IN_RESPONSE else bytes(1)
        # DEV-15: a trailing request byte is echoed after the service identifier.
        return self._prefix(MODE_STORED_DTCS, pid, body)

    def _mode09(self, pid: int | None) -> bytes | None:
        if pid is None:
            return None
        if masks.is_range_request(pid):
            supported = frozenset({INFO_VIN, INFO_ECU_NAME})
            if not masks.is_advertised_range(pid, supported):
                logger.info("OBD mode 09 range 0x%02X is not advertised; no response", pid)
                return None
            return self._prefix(MODE_VEHICLE_INFO, pid, masks.supported_mask(pid, supported))
        if pid == INFO_VIN:
            return self._prefix(MODE_VEHICLE_INFO, pid, self._vin())
        if pid == INFO_ECU_NAME:
            return self._prefix(MODE_VEHICLE_INFO, pid, self._ecu_name_field())
        return None

    # -- mode 09 fields ------------------------------------------------------------------------

    def _vin(self) -> bytes:
        """Number of data items, then the VIN, left-padded with NULs when short.

        The count is 1: a vehicle has one VIN (DEV-02). The response stays 20 bytes, so
        the multi-frame path and the tester's flow control are unaffected. Evidence in
        docs/decisions/0003-phase-5-obd-evidence.md; not standards validated.
        """
        vin = str(self.vehicle.get("vehicle.vin")).encode()[:VIN_LENGTH]
        return bytes([VIN_ITEM_COUNT]) + bytes(VIN_LENGTH - len(vin)) + vin

    def _ecu_name_field(self) -> bytes:
        """DEV-03, frozen. No count byte, and the name is left-padded with NULs.

        The available evidence suggests real ECUs do not left-pad, but it does not
        establish the replacement layout, so these bytes stay exactly as they were. Pinned
        by tests/characterization/test_mode09_pid0a_frozen.py. Do not change without the
        evidence named in docs/decisions/0003-phase-5-obd-evidence.md.
        """
        name = self.ecu_name.encode()[:ECU_NAME_LENGTH]
        return bytes(ECU_NAME_LENGTH - len(name)) + name

    # -- framing --------------------------------------------------------------------------------

    @staticmethod
    def _prefix(sid: int, pid: int | None, data: bytes) -> bytes:
        response = bytes([sid + POSITIVE_RESPONSE_OFFSET])
        if pid is None:
            return response + data
        return response + bytes([pid]) + data
