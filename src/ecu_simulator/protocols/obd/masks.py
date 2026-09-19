"""Supported-parameter masks, computed from the parameter table.

A request for parameter 0x00, 0x20, 0x40 and so on is answered with four bytes covering
the next 32 identifiers. The most significant bit of the first byte is the identifier one
above the requested range base; the least significant bit of the last byte is the range
base plus 32, which is itself the "parameters supported" identifier for the following
range, so that bit is a claim to answer the next range query.

Nothing here is hard-coded per range: the mask is derived from whichever identifiers the
configured vehicle actually supports.
"""

from __future__ import annotations

from collections.abc import Iterable

RANGE_SIZE = 32
LAST_RANGE_BASE = 0xE0
CONTINUATION_BIT = 1
FIRST_BIT_MASK = 0x80000000


def is_range_request(pid: int) -> bool:
    """True for 0x00, 0x20, ... 0xE0: the "supported parameters" identifiers."""
    return pid % RANGE_SIZE == 0


def range_bases() -> tuple[int, ...]:
    return tuple(range(0x00, LAST_RANGE_BASE + 1, RANGE_SIZE))


def supported_mask(base: int, supported: Iterable[int]) -> bytes:
    """The four-byte mask for the range starting at ``base``.

    Legacy behaviour, retained here so that introducing this module changes no bytes: the
    continuation bit is set for every range below the last, whether or not any identifier
    exists beyond it. Corrected separately under DEV-04.
    """
    members = frozenset(supported)
    mask = CONTINUATION_BIT if base < LAST_RANGE_BASE else 0
    for pid in members:
        if base < pid < base + RANGE_SIZE:
            mask |= FIRST_BIT_MASK >> (pid - base - 1)
    return mask.to_bytes(4, "big")
