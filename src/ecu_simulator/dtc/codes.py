"""How a five-character trouble code becomes the number both protocols put on the wire.

This is the code's identity as an integer, not a protocol encoding: OBD service 03 sends
it as two bytes, UDS sends it as the top two bytes of a three-byte DTC number. The
framing each of those needs lives in ``protocols/obd/dtc.py`` and
``protocols/uds/dtc.py``; only the arithmetic is here, because it is the same arithmetic.

The bit layout is the one this project has always used and the Phase 0 characterization
tests pin: two bits of group, two bits of type, then three hexadecimal digits. SAE J2012
(`J2012_202509`) is licensed and has not been read here, so nothing about this is
standards validated.
"""

from __future__ import annotations

GROUP_BITS = {"P": 0b00, "C": 0b01, "B": 0b10, "U": 0b11}
TYPE_BITS = {"0": 0b00, "1": 0b01, "2": 0b10, "3": 0b11}
CODE_LENGTH = 5


def is_valid(code: str) -> bool:
    """True when ``code`` is a trouble code this project can encode.

    The profile schema applies the same rule at load, so a configured code always passes;
    this is the encoder's own guard.
    """
    if not isinstance(code, str) or len(code) != CODE_LENGTH:
        return False
    if code[0] not in GROUP_BITS or code[1] not in TYPE_BITS:
        return False
    return all(_is_hex_digit(character) for character in code[2:])


def code_number(code: str) -> int:
    """``"B1477"`` -> ``0x9477``. The caller has already checked :func:`is_valid`."""
    high = (GROUP_BITS[code[0]] << 6) | (TYPE_BITS[code[1]] << 4) | int(code[2], 16)
    return (high << 8) | int(code[3:5], 16)


def _is_hex_digit(character: str) -> bool:
    try:
        int(character, 16)
    except ValueError:
        return False
    return True
