"""OBD-II protocol built on the parameter table and the vehicle state.

Replaces the legacy service and response modules. Every value it reports comes from
:class:`~ecu_simulator.vehicle.VehicleState` through a signal path, and the only place a
physical quantity becomes bytes is a parameter encoder. Handling a request observes state
and never advances it.

Behaviours retained deliberately, because no reviewed evidence supports changing them in
this phase, each tracked by its DEV identifier:

* modes 0x01 to 0x0A are all claimed, and a mode with no implementation answers with
  silence rather than a negative response. Mode 07 is still one of those: DEV-11 is
  fixed for Mode 04 in Phase 6 and left open for Mode 07, whose framing has no public
  worked example (docs/decisions/0004-phase-6-dtc-evidence.md);
* outside Mode 01, only the first two request bytes are examined; Mode 01 answers
  several parameters in one response (DEV-18, corrected in Phase 5.1);
* a Mode 03 request carrying a trailing byte echoes it after the service identifier
  (DEV-15);
* Mode 09 PID 0A keeps its exact bytes, including the absent count byte and the leading
  padding (DEV-03, deferred by docs/decisions/0003-phase-5-obd-evidence.md).
"""

from __future__ import annotations

import logging

from ecu_simulator.dtc import DtcStore
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.obd import dtc as obd_dtc
from ecu_simulator.protocols.obd import masks
from ecu_simulator.protocols.obd.pids import MODE01_PIDS, PidDefinition, supported_pids
from ecu_simulator.vehicle import VehicleState

logger = logging.getLogger(__name__)

POSITIVE_RESPONSE_OFFSET = 0x40

MODE_CURRENT_DATA = 0x01
MODE_STORED_DTCS = 0x03
MODE_CLEAR_DTCS = 0x04
MODE_VEHICLE_INFO = 0x09

# Claimed so that unimplemented modes keep answering with silence (DEV-11) rather than
# falling through to the ECU's unsupported-service policy, which would be an unreviewed
# wire change.
CLAIMED_SERVICE_IDS = frozenset(range(0x01, 0x0B))

# DEV-18: the ELM327 datasheet states the limit, and it is why it exists -- the service
# identifier plus six parameters is seven bytes, exactly one CAN single frame.
MAX_MODE01_PARAMETERS = 6

VIN_LENGTH = 17
VIN_ITEM_COUNT = 1  # DEV-02: one VIN per vehicle
ECU_NAME_LENGTH = 20

INFO_VIN = 0x02
INFO_ECU_NAME = 0x0A


def _parameters_to_answer(requested: bytes) -> list[int]:
    """The Mode 01 parameters to answer, in request order.

    Anything past the sixth byte is ignored, which is the six-parameter limit the ELM327
    datasheet states; before Phase 5.1 the cut-off was one rather than six, and nothing
    else about how surplus bytes are treated has changed. A parameter repeated inside the
    request is answered once, in the position of its first occurrence, so ``01 0C 0C``
    still answers ``41 0C <rpm>``. Both rules are project choices: the evidence settles
    neither. See docs/decisions/0005-phase-5-1-multi-pid-evidence.md.
    """
    answered: list[int] = []
    for pid in requested[:MAX_MODE01_PARAMETERS]:
        if pid not in answered:
            answered.append(pid)
    return answered


class ObdProtocol:
    """Serves OBD-II Mode 01, Mode 03 and Mode 09 for one ECU."""

    name = "obd"
    service_ids = CLAIMED_SERVICE_IDS

    def __init__(self, vehicle: VehicleState, *, ecu_name: str, dtcs: DtcStore | None = None) -> None:
        self.vehicle = vehicle
        self.ecu_name = ecu_name
        # The ECU's shared DTC state, not a private copy: Mode 04 clears the same object
        # UDS 0x14 clears (plan rule 7).
        self.dtcs = dtcs if dtcs is not None else DtcStore()

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
        if sid not in CLAIMED_SERVICE_IDS:
            return None
        if sid == MODE_CURRENT_DATA:
            return self._mode01(payload[1:])
        # Every other mode still reads one parameter byte: the datasheet's multi-parameter
        # rule is service 01 only, and Mode 03's trailing-byte echo is DEV-15, still frozen.
        pid = payload[1] if len(payload) >= 2 else None
        if sid == MODE_CLEAR_DTCS:
            return self._mode04()
        if sid == MODE_STORED_DTCS:
            return self._mode03(pid)
        if sid == MODE_VEHICLE_INFO:
            return self._mode09(pid)
        logger.info("OBD mode 0x%02X is not implemented; no response", sid)
        return None  # DEV-11

    # -- modes -------------------------------------------------------------------------------

    def _mode01(self, requested: bytes) -> bytes | None:
        """One response carrying every requested parameter this vehicle can answer.

        The service identifier appears once; each parameter identifier is echoed
        immediately before its own data. Both worked CAN captures in the ELM327
        datasheet's "Multiple PID Requests" section have this shape, and they are
        reproduced byte for byte by tests/unit/test_obd_protocol.py. Evidence and the
        choices the evidence does not settle are in
        docs/decisions/0005-phase-5-1-multi-pid-evidence.md; not standards validated.

        A parameter this vehicle cannot answer is left out rather than refusing the whole
        request, so a request whose only parameter is unsupported still answers with
        silence, exactly as it did before Phase 5.1.
        """
        body = bytearray()
        for pid in _parameters_to_answer(requested):
            data = self._mode01_parameter(pid)
            if data is not None:
                body += bytes([pid]) + data
        if not body:
            return None
        return bytes([MODE_CURRENT_DATA + POSITIVE_RESPONSE_OFFSET]) + bytes(body)

    def _mode01_parameter(self, pid: int) -> bytes | None:
        """The data bytes for one parameter, or ``None`` when this vehicle has no answer."""
        if masks.is_range_request(pid):
            supported = self.supported_mode01_pids
            if not masks.is_advertised_range(pid, supported):
                # Not advertised, so not answered: DEV-04 keeps the two in agreement.
                logger.info("OBD range 0x%02X is not advertised by this vehicle; no response", pid)
                return None
            return masks.supported_mask(pid, supported)
        definition = self.definition(pid)
        if definition is None:
            logger.info("OBD PID 0x%02X is not supported by this vehicle; no response", pid)
            return None
        return definition.read(self.vehicle)

    def _mode03(self, pid: int | None) -> bytes:
        """The confirmed codes in the shared store: what this service calls "stored".

        Folding "stored" into "confirmed" is a modeling decision for this project, not a
        claim that SAE or ISO define the two as equivalent; see
        docs/decisions/0004-phase-6-dtc-evidence.md and ecu_simulator.dtc.store.
        """
        # DEV-15: a trailing request byte is echoed after the service identifier.
        return self._prefix(MODE_STORED_DTCS, pid, obd_dtc.encode(self.dtcs.confirmed))

    def _mode04(self) -> bytes:
        """Clear the shared store and acknowledge with a single byte.

        The ELM327 datasheet states the response: "A response of 44 from the vehicle
        indicates that the mode request has been carried out, the information erased, and
        the MIL turned off." The clear itself is DtcStore.clear(), the same operation UDS
        0x14 calls; neither service owns the transition (plan rule 7). What that
        transition is, and why it is narrower than either protocol's description, is in
        docs/decisions/0004-phase-6-dtc-evidence.md. Not standards validated.

        A trailing request byte is ignored rather than echoed: the Mode 03 echo is DEV-15,
        an unresolved behavior, and a new service does not inherit it.
        """
        self.dtcs.clear()
        logger.info("OBD mode 04: diagnostic trouble code state cleared")
        return bytes([MODE_CLEAR_DTCS + POSITIVE_RESPONSE_OFFSET])

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
