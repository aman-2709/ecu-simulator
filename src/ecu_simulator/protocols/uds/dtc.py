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

from ecu_simulator.dtc import DtcStore, code_number, is_valid
from ecu_simulator.protocols.uds.providers import DtcRecord

# The third byte of the three-byte DTC number. In the J2012 three-byte form this is the
# failure-type byte, and the profile's five-character codes do not carry one, so whatever
# goes here is this project's choice. It stays exactly as the legacy encoder left it
# because no evidence supports any value, including this one; DEV-16 keeps that half open.
# docs/decisions/0004-phase-6-dtc-evidence.md, W9.
FAILURE_TYPE_BYTE = 0x01

# Carried over unchanged from the legacy encoder. Replaced by a value derived from the
# store in the commit that fixes the status half of DEV-16.
FIXED_STATUS = 0x2F

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
            yield DtcRecord((code_number(state.code) << 8) | FAILURE_TYPE_BYTE, FIXED_STATUS)
