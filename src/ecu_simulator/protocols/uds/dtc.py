"""UDS trouble code encoding: a three-byte number and a status byte per record.

The shared :class:`~ecu_simulator.dtc.DtcStore` holds state; this module turns a view of
it into :class:`~ecu_simulator.protocols.uds.providers.DtcRecord` values that service
0x19 puts on the wire. It reads the store and never mutates it, and the store never holds
one of these records: the UDS encoding stops here.

The provider is registered on the ECU's ``dtc_providers`` registry, which is the
extension point UDS data services read through (plan rule 6).

**Nothing here is standards validated.** ISO 14229-1:2026 is licensed and unread. The
status-byte bit layout is corroborated by the AUTOSAR Dem specification and two
independent open-source implementations; which of those bits this project is able to set
is a project decision, recorded in docs/decisions/0004-phase-6-dtc-evidence.md.
"""

from __future__ import annotations

from collections.abc import Iterator

from ecu_simulator.dtc import DtcState, DtcStore, code_number, is_valid
from ecu_simulator.protocols.uds.providers import DtcRecord

# The third byte of the three-byte DTC number. In the J2012 three-byte form this is the
# failure-type byte, and the profile's five-character codes do not carry one, so whatever
# goes here is this project's choice. It stays exactly as the legacy encoder left it
# because no evidence supports any value, including this one; DEV-16 keeps that half open.
# docs/decisions/0004-phase-6-dtc-evidence.md, W9.
FAILURE_TYPE_BYTE = 0x01

# Every status bit, as the AUTOSAR Dem specification names them and as udsoncan and Scapy
# both encode them. Listed in full so that the bits this project does not model are
# visible as a deliberate omission rather than an oversight.
TEST_FAILED = 0x01
TEST_FAILED_THIS_OPERATION_CYCLE = 0x02
PENDING_DTC = 0x04
CONFIRMED_DTC = 0x08
TEST_NOT_COMPLETED_SINCE_LAST_CLEAR = 0x10
TEST_FAILED_SINCE_LAST_CLEAR = 0x20
TEST_NOT_COMPLETED_THIS_OPERATION_CYCLE = 0x40
WARNING_INDICATOR_REQUESTED = 0x80

# The bits this simulator can set, and therefore the DTCStatusAvailabilityMask it
# advertises: pending, confirmed and warningIndicatorRequested, which is 0x8C. Derived
# from the three constants rather than written as a literal so it cannot drift away from
# what status_byte() below can produce.
#
# The five bits left out need an operation-cycle and monitor-completion model this project
# does not have. Advertising them would claim state the simulator cannot express, and a
# tester asking for, say, testFailed would get an empty answer that looked like "no faults"
# rather than "not supported". The value itself is a property of this server, not something
# any specification fixes.
AVAILABILITY_MASK = PENDING_DTC | CONFIRMED_DTC | WARNING_INDICATOR_REQUESTED


def status_byte(state: DtcState) -> int:
    """The UDS status byte for one trouble code, from the state this project models.

    Bit 2 pendingDTC, bit 3 confirmedDTC, bit 7 warningIndicatorRequested. Nothing else is
    ever set: see :data:`AVAILABILITY_MASK`.
    """
    byte = 0
    if state.pending:
        byte |= PENDING_DTC
    if state.confirmed:
        byte |= CONFIRMED_DTC
    if state.indicator_requested:
        byte |= WARNING_INDICATOR_REQUESTED
    return byte


class DtcStoreProvider:
    """Serves one ECU's shared DTC store as UDS records."""

    def __init__(self, name: str, store: DtcStore) -> None:
        self._name = name
        self.store = store

    @property
    def name(self) -> str:
        return self._name

    def dtc_records(self) -> Iterator[DtcRecord]:
        for state in self.store:
            if not is_valid(state.code):  # pragma: no cover - the schema rejects these at load
                continue
            yield DtcRecord((code_number(state.code) << 8) | FAILURE_TYPE_BYTE, status_byte(state))
