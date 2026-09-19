"""UDS (ISO 14229-1) services this simulator answers.

Replaces the frozen legacy module. The services it carries over -- 0x10
DiagnosticSessionControl, 0x11 ECUReset and 0x19 ReadDTCInformation -- answer byte for
byte what that module answered, including its known-wrong behavior: the fixed session
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

from ecu_simulator.protocols.base import (
    NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT,
    NRC_SUB_FUNCTION_NOT_SUPPORTED,
    ServiceRequest,
    negative_response,
    positive_response_sid,
)
from ecu_simulator.protocols.uds.providers import DtcRegistry

logger = logging.getLogger(__name__)

DIAGNOSTIC_SESSION_CONTROL = 0x10
ECU_RESET = 0x11
READ_DTC_INFORMATION = 0x19

SERVICE_IDS = frozenset({DIAGNOSTIC_SESSION_CONTROL, ECU_RESET, READ_DTC_INFORMATION})

# DEV-17, preserved: P2 = 0x001E (30 ms), P2* = 0x0BB8. No session state is kept and no
# timer is touched; session handling is Phase 11.
SESSION_PARAMETER_RECORD = bytes([0x00, 0x1E, 0x0B, 0xB8])
SESSION_TYPES = frozenset({0x01, 0x02, 0x03, 0x04})

RESET_TYPES = frozenset(range(0x01, 0x06))
RESET_ENABLE_RAPID_POWER_SHUT_DOWN = 0x04
RESET_POWER_DOWN_TIME = 0x0F

REPORT_DTC_BY_STATUS_MASK = 0x02

# Carried over unchanged; replaced by a mask derived from the modelled status bits in the
# commit that fixes the status half of DEV-16.
STATUS_AVAILABILITY_MASK = 0xFF


class UdsProtocol:
    """Serves UDS 0x10, 0x11 and 0x19 for one ECU."""

    name = "uds"
    service_ids = SERVICE_IDS

    def __init__(self, *, dtc_providers: DtcRegistry | None = None) -> None:
        self.dtc_providers = dtc_providers if dtc_providers is not None else DtcRegistry()

    def handle(self, request: ServiceRequest) -> bytes | None:
        payload = request.payload
        sid = payload[0]
        if sid == DIAGNOSTIC_SESSION_CONTROL:
            return self._session_control(payload)
        if sid == ECU_RESET:
            return self._ecu_reset(payload)
        if sid == READ_DTC_INFORMATION:
            return self._read_dtc_information(payload)
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

        DEV-05 preserved at this step: sub-function 0x02 still requires exactly two bytes.
        """
        if len(payload) < 2:
            return self._nrc(READ_DTC_INFORMATION, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        report_type = payload[1]
        if report_type != REPORT_DTC_BY_STATUS_MASK:
            return self._nrc(READ_DTC_INFORMATION, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        if len(payload) != 2:
            return self._nrc(READ_DTC_INFORMATION, NRC_INCORRECT_MESSAGE_LENGTH_OR_INVALID_FORMAT)
        header = bytes([positive_response_sid(READ_DTC_INFORMATION), report_type, STATUS_AVAILABILITY_MASK])
        records = self.dtc_providers.read(STATUS_AVAILABILITY_MASK)
        return header + b"".join(record.to_bytes() for record in records)

    # -- framing ---------------------------------------------------------------------------------

    @staticmethod
    def _nrc(sid: int, nrc: int) -> bytes:
        logger.info("UDS SID 0x%02X: negative response 0x%02X", sid, nrc)
        return negative_response(sid, nrc)
