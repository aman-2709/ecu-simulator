"""OBD-II trouble code encoding: a count byte, then two bytes per code.

Service 03 reports the confirmed codes in the ECU's shared
:class:`~ecu_simulator.dtc.DtcStore`; the store holds state, this module turns a view of
it into bytes and nothing here mutates anything.

The count byte is the CAN form. The ELM327 datasheet describes it: "the ISO 15765-4 (CAN)
protocol ... adds an extra data byte (in the second position), showing how many data items
(DTCs) are to follow". SAE J1979 itself is licensed and unread, so this is not standards
validated. See docs/decisions/0004-phase-6-dtc-evidence.md.
"""

from __future__ import annotations

from collections.abc import Iterable

from ecu_simulator.dtc import DtcState, code_number, is_valid

MAX_DTCS_IN_RESPONSE = 255


def encode(states: Iterable[DtcState]) -> bytes:
    """Count byte followed by each code as two bytes, in store order.

    More codes than the count byte can express are not reported at all, which is what
    this project has always done rather than truncate the list and misreport the count.
    """
    numbers = [code_number(state.code) for state in states if is_valid(state.code)]
    if not 0 < len(numbers) <= MAX_DTCS_IN_RESPONSE:
        return bytes(1)
    return bytes([len(numbers)]) + b"".join(number.to_bytes(2, "big") for number in numbers)
