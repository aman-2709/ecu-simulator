"""UDS (ISO 14229-1) services this simulator answers.

Replaces the frozen legacy module. The services carried over from it -- 0x10
DiagnosticSessionControl, 0x11 ECUReset and 0x19 ReadDTCInformation -- answered byte for
byte what that module answered when it was replaced, including its known-wrong behavior: the fixed session
parameter record (DEV-17) and the unmasked ``suppressPosRspMsgIndicationBit`` (DEV-07)
are both out of Phase 6's scope and are preserved deliberately, each pinned by a test.

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
from ecu_simulator.protocols.uds.providers import DtcRegistry

logger = logging.getLogger(__name__)

DIAGNOSTIC_SESSION_CONTROL = 0x10
ECU_RESET = 0x11
CLEAR_DIAGNOSTIC_INFORMATION = 0x14
READ_DTC_INFORMATION = 0x19
TESTER_PRESENT = 0x3E

SERVICE_IDS = frozenset(
    {
        DIAGNOSTIC_SESSION_CONTROL,
        ECU_RESET,
        CLEAR_DIAGNOSTIC_INFORMATION,
        READ_DTC_INFORMATION,
        TESTER_PRESENT,
    }
)

# DEV-17, preserved: P2 = 0x001E (30 ms), P2* = 0x0BB8. No session state is kept and no
# timer is touched; session handling is Phase 11.
SESSION_PARAMETER_RECORD = bytes([0x00, 0x1E, 0x0B, 0xB8])
SESSION_TYPES = frozenset({0x01, 0x02, 0x03, 0x04})

RESET_TYPES = frozenset(range(0x01, 0x06))
RESET_ENABLE_RAPID_POWER_SHUT_DOWN = 0x04
RESET_POWER_DOWN_TIME = 0x0F

REPORT_DTC_BY_STATUS_MASK = 0x02

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
    """Serves UDS 0x10, 0x11, 0x14, 0x19 and 0x3E for one ECU."""

    name = "uds"
    service_ids = SERVICE_IDS

    def __init__(self, *, dtc_providers: DtcRegistry | None = None, dtcs: DtcStore | None = None) -> None:
        # Reads go through the provider registry (plan rule 6); the clear goes straight to
        # the shared store, which is the same object OBD Mode 04 clears (plan rule 7).
        self.dtc_providers = dtc_providers if dtc_providers is not None else DtcRegistry()
        self.dtcs = dtcs if dtcs is not None else DtcStore()

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
        payload = request.payload
        sid = payload[0]
        suppress = False
        if sid in SUB_FUNCTION_SERVICES and len(payload) > 1:
            suppress = bool(payload[1] & SUPPRESS_POS_RSP_MSG_INDICATION_BIT)
            payload = bytes([sid, payload[1] & SUB_FUNCTION_VALUE_MASK]) + payload[2:]
        response = self._serve(sid, payload)
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
        if sid == TESTER_PRESENT:
            return self._tester_present(payload)
        return None  # pragma: no cover - the ECU only routes claimed SIDs here

    # -- 0x10 ------------------------------------------------------------------------------------

    def _session_control(self, payload: bytes) -> bytes:
        """DEV-07 preserved: 0x80 is not masked off, so ``10 81`` is an unknown sub-function."""
        if len(payload) != 2:
            return self._nrc(DIAGNOSTIC_SESSION_CONTROL, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        session = payload[1]
        if session not in SESSION_TYPES:
            return self._nrc(DIAGNOSTIC_SESSION_CONTROL, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        return bytes([positive_response_sid(DIAGNOSTIC_SESSION_CONTROL), session]) + SESSION_PARAMETER_RECORD

    # -- 0x11 ------------------------------------------------------------------------------------

    def _ecu_reset(self, payload: bytes) -> bytes:
        """DEV-07 preserved here too."""
        if len(payload) != 2:
            return self._nrc(ECU_RESET, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        reset_type = payload[1]
        if reset_type not in RESET_TYPES:
            return self._nrc(ECU_RESET, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        response = bytes([positive_response_sid(ECU_RESET), reset_type])
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
        sub-function behind a length error.

        **The order is a project choice.** ISO 14229-1:2026 is licensed and unread, and no
        accessible source states the order in which a server checks service, sub-function
        and length. This one is chosen to keep every currently pinned negative response
        unchanged except where correcting DEV-05 forces a change.

        Sub-function 0x02 takes a DTCStatusMask, so its request is exactly three bytes
        (DEV-05). A record is reported when ``(status & DTCStatusMask) != 0``; when
        nothing matches, the response is the header alone.

        The DTCStatusAvailabilityMask that opens the response is the set of status bits
        this server can actually set, which is what the AUTOSAR Dem describes that value
        as; here it is 0x8C (DEV-16). Because every status byte only ever contains those
        bits, filtering against the client's raw mask already restricts the comparison to
        supported bits.

        The request shape and the filter rule are corroborated by the AUTOSAR Dcm
        specification, which states the rule in those words, and by two independent
        open-source implementations that both treat the mask as mandatory. ISO 14229-1
        itself is unread, so this is not standards validated.
        """
        if len(payload) < 2:
            return self._nrc(READ_DTC_INFORMATION, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        report_type = payload[1]
        if report_type != REPORT_DTC_BY_STATUS_MASK:
            return self._nrc(READ_DTC_INFORMATION, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        if len(payload) != 3:
            return self._nrc(READ_DTC_INFORMATION, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        status_mask = payload[2]
        header = bytes([positive_response_sid(READ_DTC_INFORMATION), report_type, AVAILABILITY_MASK])
        records = self.dtc_providers.read(status_mask)
        return header + b"".join(record.to_bytes() for record in records)

    # -- 0x3E ------------------------------------------------------------------------------------

    def _tester_present(self, payload: bytes) -> bytes:
        """``3E 00`` is answered ``7E 00``. Stateless: no timer, no session, no counter.

        TesterPresent exists in ISO 14229 to keep a non-default session alive by resetting
        the S3 timer. This simulator has no session state and no timer, so the service is
        implemented as what it is on the wire and nothing more: the request is validated,
        the sub-function is echoed back, and the server's state is exactly what it was
        before. S3, session timing and Concurrent TesterPresent are Phase 11.

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
