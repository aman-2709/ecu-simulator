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


def has_supported_above(base: int, supported: Iterable[int]) -> bool:
    """True when any supported identifier lies beyond the range starting at ``base``."""
    limit = base + RANGE_SIZE
    return any(pid > limit for pid in supported)


def supported_mask(base: int, supported: Iterable[int]) -> bytes:
    """The four-byte mask for the range starting at ``base``.

    The continuation bit is set only when the vehicle actually has an identifier beyond
    this range, so the advertised chain ends after the last populated range (DEV-04). Real
    ECUs behave this way: the ELM327 datasheet publishes one capture of ``01 00`` in which
    the engine sets the bit and has parameters beyond, while the transmission clears it and
    has none. Derived from the parameter table, never hard-coded per range.
    """
    members = frozenset(supported)
    mask = CONTINUATION_BIT if base < LAST_RANGE_BASE and has_supported_above(base, members) else 0
    for pid in members:
        if base < pid < base + RANGE_SIZE:
            mask |= FIRST_BIT_MASK >> (pid - base - 1)
    return mask.to_bytes(4, "big")


def is_advertised_range(base: int, supported: Iterable[int]) -> bool:
    """True when a tester following the chain would reach ``base``.

    The first range is always reachable. A later one is reachable only if every preceding
    range set its continuation bit, which keeps what the simulator advertises and what it
    answers in agreement.
    """
    if not is_range_request(base) or not 0 <= base <= LAST_RANGE_BASE:
        return False
    members = frozenset(supported)
    return all(has_supported_above(earlier, members) for earlier in range(0x00, base, RANGE_SIZE))
