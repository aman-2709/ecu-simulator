"""The Rocksoft-model CRC that the generic rule-file algorithms and the AUTOSAR E2E presets share."""

from collections.abc import Iterable


def crc(data: Iterable[int], width: int, poly: int, init: int, xorout: int, reflect: bool) -> int:
    """The Rocksoft-model CRC of ``data``: ``reflect`` applies to each input byte and to the final register."""
    top, mask = 1 << (width - 1), (1 << width) - 1
    register = init
    for byte in data:
        register ^= (_reverse(byte, 8) if reflect else byte) << (width - 8)
        for _ in range(8):
            register = ((register << 1) ^ poly if register & top else register << 1) & mask
    return (_reverse(register, width) if reflect else register) ^ xorout


def _reverse(value: int, bits: int) -> int:
    """``value`` with its ``bits`` low bits in reverse order."""
    return int(f"{value:0{bits}b}"[::-1], 2)
