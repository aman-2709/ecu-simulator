"""UDS (ISO 14229-1) services this simulator answers.

Replaces the frozen legacy module. The services carried over from it -- 0x10
DiagnosticSessionControl, 0x11 ECUReset and 0x19 ReadDTCInformation -- answered byte for
byte what that module answered when it was replaced. Phase 7 added 0x3E and the generic
``suppressPosRspMsgIndicationBit`` handling (DEV-07). Phase 11 added session state, S3,
0x22 and 0x19 report types 0x01 and 0x0A; see
docs/decisions/0011-phase-11-uds-session-handling.md.

DTC data is read through the ECU's ``dtc_providers`` registry rather than owned here
(plan rule 6); the records come from the shared
:class:`~ecu_simulator.dtc.DtcStore` by way of
:class:`~ecu_simulator.protocols.uds.dtc.DtcStoreProvider`.

**Nothing here is standards validated.** ISO 14229-1:2026 (Edition 4, published
2026-06-05) is licensed and has not been read by this project. Evidence for every shape
in this module is recorded in docs/decisions/0004-phase-6-dtc-evidence.md.
"""

from __future__ import annotations

import logging

from ecu_simulator.dtc import DtcStore
from ecu_simulator.protocols.base import (
    NEGATIVE_RESPONSE_SID,
    NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT,
    NRC_REQUEST_OUT_OF_RANGE,
    NRC_SUB_FUNCTION_NOT_SUPPORTED,
    ServiceRequest,
    negative_response,
    positive_response_sid,
)
from ecu_simulator.protocols.uds.dtc import AVAILABILITY_MASK
from ecu_simulator.protocols.uds.providers import DidRegistry, DtcRegistry
from ecu_simulator.protocols.uds.session import UdsSession

logger = logging.getLogger(__name__)

DIAGNOSTIC_SESSION_CONTROL = 0x10
ECU_RESET = 0x11
CLEAR_DIAGNOSTIC_INFORMATION = 0x14
READ_DTC_INFORMATION = 0x19
READ_DATA_BY_IDENTIFIER = 0x22
TESTER_PRESENT = 0x3E

SERVICE_IDS = frozenset(
    {
        DIAGNOSTIC_SESSION_CONTROL,
        ECU_RESET,
        CLEAR_DIAGNOSTIC_INFORMATION,
        READ_DTC_INFORMATION,
        READ_DATA_BY_IDENTIFIER,
        TESTER_PRESENT,
    }
)

NRC_RESPONSE_TOO_LONG = 0x14
# The largest message a classical ISO-TP first frame's 12-bit length field expresses, and
# so the largest every ISO-TP receiver can take. The kernel sends more only with the
# escaped first frame of ISO 15765-2:2016. docs/decisions/0011, "The 0x22 length limit".
MAX_RESPONSE_LENGTH = 4095
DID_LENGTH = 2

RESET_TYPES = frozenset(range(0x01, 0x06))
RESET_ENABLE_RAPID_POWER_SHUT_DOWN = 0x04
# hardReset, keyOffOnReset, softReset: the reset types that restart the ECU.
RESETS_THAT_RESTART = frozenset({0x01, 0x02, 0x03})
RESET_POWER_DOWN_TIME = 0x0F

REPORT_NUMBER_OF_DTC_BY_STATUS_MASK = 0x01
REPORT_DTC_BY_STATUS_MASK = 0x02
REPORT_SUPPORTED_DTCS = 0x0A
# The report types served, and the exact request length each takes: 0x01 and 0x02 carry a
# DTCStatusMask, 0x0A carries nothing ([SWS_Dcm_01643], [SWS_Dcm_01645]).
REPORT_REQUEST_LENGTHS = {
    REPORT_NUMBER_OF_DTC_BY_STATUS_MASK: 3,
    REPORT_DTC_BY_STATUS_MASK: 3,
    REPORT_SUPPORTED_DTCS: 2,
}
# DTCFormatIdentifier in the 0x01 response: SAE_J2012-DA_DTCFormat_04, the three-byte J2012
# form, whose first two bytes are what this simulator encodes (ruling Q7). AUTOSAR's
# Dem_DTCTranslationFormatType gives the value ([SWS_Dem_00936]). The third byte is still
# the frozen 0x01 of DEV-16, which this value makes read as a failure type.
DTC_FORMAT_IDENTIFIER = 0x04

# The only groupOfDTC this simulator serves: all of them. Anything else is answered
# requestOutOfRange rather than guessed at, because this project has no evidence for which
# codes belong to which group and the profile does not say. docs/decisions/0004, W5.
GROUP_OF_DTC_ALL = 0xFFFFFF
CLEAR_REQUEST_LENGTH = 4

# Which services have a sub-function, and therefore for which of them the
# suppressPosRspMsgIndicationBit exists at all. AUTOSAR gates the whole handling on this
# ([SWS_Dcm_00204]) and makes it per-service configuration rather than something derived
# from the request: [ECUC_Dcm_00737] DcmDsdSidTabSubfuncAvail, "true - service has
# subfunctions, suppressPosRspMsgIndicationBit is available". This table is that
# configuration. It is never inferred from the shape of a payload, because a payload byte
# in bit-7 position is not evidence of anything -- 0x14's groupOfDTC 0xFFFFFF has bit 7
# set in exactly that position and must not be touched. 0x19 is here because
# ReadDTCInformation does have a sub-function, the report type, whatever Phase 6 answered
# for 19 82 FF before this rule existed; declaring it otherwise would keep those bytes by
# recording something untrue about the service.
SUB_FUNCTION_SERVICES = frozenset(
    {DIAGNOSTIC_SESSION_CONTROL, ECU_RESET, READ_DTC_INFORMATION, TESTER_PRESENT}
)

# Bit 7 of the sub-function byte: "do not send me a positive response".
SUPPRESS_POS_RSP_MSG_INDICATION_BIT = 0x80
SUB_FUNCTION_VALUE_MASK = 0x7F

# 0x3E's only sub-function. AUTOSAR [SWS_Dcm_00251] names 0x00 and 0x80 as the service's
# two values; 0x80 is 0x00 with the suppressPosRspMsgIndicationBit set, which is a framing
# rule about sub-functions rather than a second sub-function, and is handled before this
# service sees the byte. Nothing here needs to know about it.
ZERO_SUB_FUNCTION = 0x00
TESTER_PRESENT_REQUEST_LENGTH = 2


class UdsProtocol:
    """Serves UDS 0x10, 0x11, 0x14, 0x19, 0x22 and 0x3E for one ECU."""

    name = "uds"
    service_ids = SERVICE_IDS

    def __init__(
        self,
        *,
        dtc_providers: DtcRegistry | None = None,
        dtcs: DtcStore | None = None,
        session: UdsSession | None = None,
        dids: DidRegistry | None = None,
    ) -> None:
        # Reads go through the provider registry (plan rule 6); the clear goes straight to
        # the shared store, which is the same object OBD Mode 04 clears (plan rule 7).
        self.dtc_providers = dtc_providers if dtc_providers is not None else DtcRegistry()
        self.dtcs = dtcs if dtcs is not None else DtcStore()
        self.session = session if session is not None else UdsSession()
        self.dids = dids if dids is not None else DidRegistry()

    def handle(self, request: ServiceRequest) -> bytes | None:
        """Apply the sub-function framing rules, run the service, then decide what goes out.

        The suppressPosRspMsgIndicationBit is bit 7 of the sub-function byte and is not a
        sub-function value. It is handled here, once, for every service the table above
        declares to have a sub-function, so that no service handler contains a copy of the
        rule and no handler can disagree with another about it. The order matters and is
        AUTOSAR's:

        1. does this service have a sub-function at all? If not, nothing below happens and
           the payload reaches the service untouched ([SWS_Dcm_00204]);
        2. read the suppression intent from bit 7, and remember it;
        3. mask the bit off before the service sees the byte ([SWS_Dcm_00201]), so a
           sub-function is matched on its value and a service never learns about the bit;
        4. run the service normally, whatever it is;
        5. withhold the response only if it is a positive one ([SWS_Dcm_00200]).

        Step 5 is the part that is easy to get wrong. A response is not withheld because
        bit 7 was set; it is withheld because bit 7 was set *and* the service succeeded.
        A sub-function this server does not support, or a malformed request, still gets
        its negative response -- a tester that suppressed the positive answer is asking
        for silence on success, not for its errors to be hidden.

        ISO 14229-1:2026 clause 6.5 is the normative home of this rule and is licensed and
        unread; the rule above is taken from three named AUTOSAR requirements, which is
        documentary evidence and not a conformance claim. See docs/decisions/0006, row W2.
        """
        self.session.expire_if_idle()
        payload = request.payload
        sid = payload[0]
        suppress = False
        if sid in SUB_FUNCTION_SERVICES and len(payload) > 1:
            suppress = bool(payload[1] & SUPPRESS_POS_RSP_MSG_INDICATION_BIT)
            payload = bytes([sid, payload[1] & SUB_FUNCTION_VALUE_MASK]) + payload[2:]
        response = self._serve(sid, payload)
        # Every request this protocol handles restarts S3, positive, negative or suppressed:
        # [ECUC_Dcm_01240] measures S3 "while not receiving any diagnostic request message".
        self.session.note_request()
        if suppress and response is not None and response[0] != NEGATIVE_RESPONSE_SID:
            logger.info("UDS SID 0x%02X: positive response suppressed at the tester's request", sid)
            return None
        return response

    def _serve(self, sid: int, payload: bytes) -> bytes | None:
        if sid == DIAGNOSTIC_SESSION_CONTROL:
            return self._session_control(payload)
        if sid == ECU_RESET:
            return self._ecu_reset(payload)
        if sid == CLEAR_DIAGNOSTIC_INFORMATION:
            return self._clear_diagnostic_information(payload)
        if sid == READ_DTC_INFORMATION:
            return self._read_dtc_information(payload)
        if sid == READ_DATA_BY_IDENTIFIER:
            return self._read_data_by_identifier(payload)
        if sid == TESTER_PRESENT:
            return self._tester_present(payload)
        return None  # pragma: no cover - the ECU only routes claimed SIDs here

    # -- 0x10 ------------------------------------------------------------------------------------

    def _session_control(self, payload: bytes) -> bytes:
        """Answer with the requested session's timing record, then make that session active.

        A session this ECU is not configured with is subFunctionNotSupported and changes
        nothing ([SWS_Dcm_00307]). A configured one is entered even when it is already
        active, and only after its response has been produced ([SWS_Dcm_00311] sets the new
        session in the send confirmation). The record is P2ServerMax in 1 ms units and
        P2*ServerMax in 10 ms units (W4); its values come from configuration and default to
        the 00 1E 0B B8 this service has always sent (DEV-17, ruling Q3).
        """
        if len(payload) != 2:
            return self._nrc(DIAGNOSTIC_SESSION_CONTROL, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        session = payload[1]
        if session not in self.session.supported:
            return self._nrc(DIAGNOSTIC_SESSION_CONTROL, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        response = bytes([positive_response_sid(DIAGNOSTIC_SESSION_CONTROL), session])
        response += self.session.timing(session).record()
        self.session.enter(session)
        return response

    # -- 0x11 ------------------------------------------------------------------------------------

    def _ecu_reset(self, payload: bytes) -> bytes:
        """Answer the reset; for a reset that restarts the ECU, return to the default session.

        hardReset, keyOffOnReset and softReset (0x01-0x03) request a reset; AUTOSAR maps
        them to the DcmEcuReset mode and executes it after the positive response
        ([SWS_Dcm_00373], [SWS_Dcm_00594]), and a restarted Dcm is in the default session
        ([SWS_Dcm_00034]). enableRapidPowerShutDown and disableRapidPowerShutDown (0x04,
        0x05) only switch a power-down mode ([SWS_Dcm_00818]) and leave the session alone.

        Not modelled (ruling Q8): the period after the response in which a resetting ECU
        ignores requests ([SWS_Dcm_00834]), for which no accessible source gives a length;
        an unresponsive ECU is Phase 10's fault injection. The DTC store and the scenario
        are untouched: fault memory is not volatile, and vehicle state is not the ECU's.
        """
        if len(payload) != 2:
            return self._nrc(ECU_RESET, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        reset_type = payload[1]
        if reset_type not in RESET_TYPES:
            return self._nrc(ECU_RESET, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        response = bytes([positive_response_sid(ECU_RESET), reset_type])
        if reset_type in RESETS_THAT_RESTART:
            self.session.return_to_default(f"ECUReset 0x{reset_type:02X}")
        if reset_type == RESET_ENABLE_RAPID_POWER_SHUT_DOWN:
            return response + bytes([RESET_POWER_DOWN_TIME])
        return response

    # -- 0x14 ------------------------------------------------------------------------------------

    def _clear_diagnostic_information(self, payload: bytes) -> bytes:
        """Clear the shared store for groupOfDTC 0xFFFFFF; answer 0x54 with no data.

        The request is the service identifier and a three-byte groupOfDTC. Only "all DTCs"
        is served: which trouble codes belong to any narrower group is not something this
        project can determine, so a different group is answered requestOutOfRange rather
        than guessed at or silently treated as "all". The five-byte MemorySelection form
        introduced in ISO 14229-1:2020 is refused on length for the same reason -- this
        simulator has one fault memory, and accepting a selector it cannot honour would be
        worse than refusing it.

        The clear itself is DtcStore.clear(), the same operation OBD Mode 04 calls; what
        that transition is, and why it is narrower than either protocol's description of a
        clear, is in docs/decisions/0004-phase-6-dtc-evidence.md. Request shape, empty
        positive response and the 0x31 choice are each corroborated by the AUTOSAR Dcm
        specification and two independent open-source implementations. ISO 14229-1 is
        unread, so none of it is standards validated.
        """
        if len(payload) != CLEAR_REQUEST_LENGTH:
            return self._nrc(CLEAR_DIAGNOSTIC_INFORMATION, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        group = int.from_bytes(payload[1:4], "big")
        if group != GROUP_OF_DTC_ALL:
            logger.info("UDS 0x14: groupOfDTC 0x%06X is not served by this simulator", group)
            return self._nrc(CLEAR_DIAGNOSTIC_INFORMATION, NRC_REQUEST_OUT_OF_RANGE)
        self.dtcs.clear()
        logger.info("UDS 0x14: diagnostic trouble code state cleared")
        return bytes([positive_response_sid(CLEAR_DIAGNOSTIC_INFORMATION)])

    # -- 0x19 ------------------------------------------------------------------------------------

    def _read_dtc_information(self, payload: bytes) -> bytes:
        """Sub-function first, then the length that sub-function requires.

        A 0x19 request needs at least a sub-function byte; without one there is nothing
        to dispatch on and the answer is a length error. With one, the sub-function
        decides whether the service is supported at all, and only then does its own
        length rule apply. The order matters because sub-functions take different numbers
        of parameters, and checking one global length first hides an unsupported
        sub-function behind a length error. **The order is a project choice**, made in
        Phase 6 and kept: no accessible source states it.

        Three report types are served, each opening with the DTCStatusAvailabilityMask
        0x8C (DEV-16):

        * **0x02** reportDTCByStatusMask (DEV-05): the records with
          ``(status & DTCStatusMask) != 0``, or the header alone when nothing matches.
        * **0x01** reportNumberOfDTCByStatusMask (Phase 11): the DTCFormatIdentifier and a
          two-byte count of the records 0x02 would report for the same mask
          ([SWS_Dcm_01642], [SWS_Dcm_01643]).
        * **0x0A** reportSupportedDTCs (Phase 11): a record for every configured code, in
          profile order, including those whose status is 0x00; the filter's mask is 0x00,
          so nothing is filtered out ([SWS_Dcm_01644], [SWS_Dcm_01645]).

        Corroborated by the AUTOSAR Dcm specification and by what udsoncan and Scapy parse.
        ISO 14229-1 is unread, so none of this is standards validated.
        """
        if len(payload) < 2:
            return self._nrc(READ_DTC_INFORMATION, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        report_type = payload[1]
        required_length = REPORT_REQUEST_LENGTHS.get(report_type)
        if required_length is None:
            return self._nrc(READ_DTC_INFORMATION, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        if len(payload) != required_length:
            return self._nrc(READ_DTC_INFORMATION, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        header = bytes([positive_response_sid(READ_DTC_INFORMATION), report_type, AVAILABILITY_MASK])
        if report_type == REPORT_SUPPORTED_DTCS:
            return header + b"".join(record.to_bytes() for record in self.dtc_providers.records())
        records = self.dtc_providers.read(payload[2])
        if report_type == REPORT_NUMBER_OF_DTC_BY_STATUS_MASK:
            return header + bytes([DTC_FORMAT_IDENTIFIER]) + len(records).to_bytes(2, "big")
        return header + b"".join(record.to_bytes() for record in records)

    # -- 0x22 ------------------------------------------------------------------------------------

    def _read_data_by_identifier(self, payload: bytes) -> bytes:
        """``22`` and one or more two-byte DIDs; ``62`` and each supported DID with its data.

        A request that is not the service identifier and a whole number of at least one DID
        is a length error ([SWS_Dcm_00696], [SWS_Dcm_00272]). DIDs are answered in request
        order, each echoed before its data, which is what udsoncan parses and
        driftregion/iso14229 builds; a DID asked twice is answered twice.

        A DID this ECU does not serve is left out of the response, and only when none of
        them is served is the request refused with requestOutOfRange: "If none of the
        requested DIDs is supported, the Dcm module shall send NRC 0x31" ([SWS_Dcm_00438]).
        driftregion refuses the whole request instead; the AUTOSAR text ranks higher
        (docs/decisions/0011, W7). A response longer than :data:`MAX_RESPONSE_LENGTH` is
        responseTooLong ([SWS_Dcm_01059]). No DID is session-gated until Phase 12.
        """
        if len(payload) < 1 + DID_LENGTH or (len(payload) - 1) % DID_LENGTH:
            return self._nrc(READ_DATA_BY_IDENTIFIER, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        response = bytearray([positive_response_sid(READ_DATA_BY_IDENTIFIER)])
        answered = False
        for offset in range(1, len(payload), DID_LENGTH):
            did_bytes = payload[offset : offset + DID_LENGTH]
            data = self.dids.read(int.from_bytes(did_bytes, "big"))
            if data is None:
                logger.info("UDS 0x22: DID 0x%s is not served; omitted", did_bytes.hex().upper())
                continue
            response += did_bytes + data
            answered = True
        if not answered:
            return self._nrc(READ_DATA_BY_IDENTIFIER, NRC_REQUEST_OUT_OF_RANGE)
        if len(response) > MAX_RESPONSE_LENGTH:
            return self._nrc(READ_DATA_BY_IDENTIFIER, NRC_RESPONSE_TOO_LONG)
        return bytes(response)

    # -- 0x3E ------------------------------------------------------------------------------------

    def _tester_present(self, payload: bytes) -> bytes:
        """``3E 00`` is answered ``7E 00``.

        TesterPresent exists to keep a non-default session alive by restarting S3. Since
        Phase 11 it does, but not here: every request this protocol handles restarts S3
        (``handle``), and TesterPresent is simply the request that does nothing else. The
        handler validates the request and echoes the sub-function. Concurrent TesterPresent,
        functionally addressed, is not reachable: no shipped route enables UDS functionally.

        The sub-function is checked before the length that sub-function requires, which is
        the ordering Phase 6 chose for 0x19. A one-byte request carries no sub-function to
        dispatch on at all, so that is a length error.

        ISO 14229-1:2026 is licensed and unread. The request and response shapes are
        corroborated by the AUTOSAR Dcm specification and, independently, by what udsoncan
        builds and what Scapy parses; nothing here is standards validated. See
        docs/decisions/0006-phase-7-scenario-and-testerpresent.md, rows W1, W3 and W4.
        """
        if len(payload) < TESTER_PRESENT_REQUEST_LENGTH:
            return self._nrc(TESTER_PRESENT, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        sub_function = payload[1]
        if sub_function != ZERO_SUB_FUNCTION:
            return self._nrc(TESTER_PRESENT, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        if len(payload) != TESTER_PRESENT_REQUEST_LENGTH:
            return self._nrc(TESTER_PRESENT, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        return bytes([positive_response_sid(TESTER_PRESENT), sub_function])

    # -- framing ---------------------------------------------------------------------------------

    @staticmethod
    def _nrc(sid: int, nrc: int) -> bytes:
        logger.info("UDS SID 0x%02X: negative response 0x%02X", sid, nrc)
        return negative_response(sid, nrc)
