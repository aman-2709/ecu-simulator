"""The generic checksum algorithms a rule file can ask for.

``build_checksum`` turns the ``checksum`` section of a rule (a ``ChecksumSpec``) into a function with the
``ecu_simulator.car.checksums.ChecksumFn`` signature, so the integrity engine runs it exactly as it runs the Toyota,
Honda and Hyundai rules: the function reads the frame with its own checksum field still zero and returns the field's
new value.

For the generic algorithms, the covered bytes are the ``covers`` list in the order given, or by default every byte
that does not overlap the checksum field; a ``data_id`` is one more byte after them. The algorithms:

- ``xor``: the covered bytes XORed together, then ``^ init``, then ``^ xorout``.
- ``sum8``: ``(init + sum of the bytes) & 0xFF``, then ``^ xorout``.
- ``nibble_sum``: ``(init - sum of both nibbles of every byte) & 0xF``, then ``^ xorout``.
- ``crc8`` and ``crc16``: the Rocksoft model, parameterised by ``poly``, ``init``, ``xorout`` and ``reflect``, which
  reflects both the input bytes and the final register. The catalogue values over ``123456789`` (for example
  CRC-16/XMODEM 0x31C3) are pinned in the tests.
- ``e2e_p01`` and ``e2e_p02``: the AUTOSAR E2E Profile 1 and Profile 2 CRCs, built by ``ecu_simulator.car.e2e``,
  which documents them and their sources. They take a Data ID (``data_id`` and ``data_id_mode``, or
  ``data_id_list``) and nothing else: the profile fixes the CRC, the covered bytes and the positions.

The result is masked to the width of the checksum field. A spec arrives already validated against its algorithm (see
``ecu_simulator.car.rules``): ``poly``, ``init`` and ``xorout`` fit the algorithm's width, ``poly`` is present for a
CRC, and every ``covers`` index is a valid byte of the message the rule is bound to.
"""

from collections.abc import Callable, Mapping, Sequence
from functools import reduce
from operator import xor
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from ecu_simulator.car.bits import BitField
from ecu_simulator.car.checksums import ChecksumFn
from ecu_simulator.car.crc import crc
from ecu_simulator.car.e2e import e2e_p01, e2e_p02

if TYPE_CHECKING:
    from ecu_simulator.car.rules import ChecksumSpec

Algorithm = Literal["xor", "sum8", "nibble_sum", "crc8", "crc16", "e2e_p01", "e2e_p02"]

# The width of each algorithm's result, which bounds its ``poly``, ``init`` and ``xorout``.
ALGORITHM_BITS: Mapping[Algorithm, int] = MappingProxyType(
    {"xor": 8, "sum8": 8, "nibble_sum": 4, "crc8": 8, "crc16": 16, "e2e_p01": 8, "e2e_p02": 8}
)
CRC_ALGORITHMS: frozenset[Algorithm] = frozenset({"crc8", "crc16"})
# The presets fix their own CRC, coverage and positions; a rule names only their Data ID parameters.
E2E_ALGORITHMS: frozenset[Algorithm] = frozenset({"e2e_p01", "e2e_p02"})

_Reduction = Callable[[Sequence[int]], int]


def build_checksum(spec: "ChecksumSpec", field_size: int) -> ChecksumFn:
    """The checksum function for ``spec``, writing a field of ``field_size`` bits."""
    if field_size < 1:
        raise ValueError(f"A checksum field needs a field size of 1 or more bits, got {field_size}")
    if spec.algorithm == "e2e_p01":
        return e2e_p01(spec, field_size)
    if spec.algorithm == "e2e_p02":
        return e2e_p02(spec, field_size)
    mask = (1 << field_size) - 1
    reduction = _reduction(spec)
    covers = None if spec.covers is None else tuple(spec.covers)
    tail = () if spec.data_id is None else (spec.data_id,)

    def checksum(address: int, data: bytearray, field: BitField, signal_name: str) -> int:
        indexes = _default_covers(len(data), field) if covers is None else covers
        return reduction([*(data[i] for i in indexes), *tail]) & mask

    return checksum


def _default_covers(size: int, field: BitField) -> list[int]:
    """Every byte of a ``size``-byte frame that holds no bit of ``field``."""
    first, last = sorted((field.msb // 8, field.lsb // 8))
    return [i for i in range(size) if not first <= i <= last]


def _reduction(spec: "ChecksumSpec") -> _Reduction:
    init, xorout = spec.init, spec.xorout
    match spec.algorithm:
        case "xor":
            return lambda data: reduce(xor, data, 0) ^ init ^ xorout
        case "sum8":
            return lambda data: ((init + sum(data)) & 0xFF) ^ xorout
        case "nibble_sum":
            return lambda data: ((init - sum((b >> 4) + (b & 0xF) for b in data)) & 0xF) ^ xorout
        case "crc8" | "crc16":
            if spec.poly is None:
                raise ValueError(f"A {spec.algorithm} needs a poly")
            width, poly, reflect = ALGORITHM_BITS[spec.algorithm], spec.poly, spec.reflect
            return lambda data: crc(data, width, poly, init, xorout, reflect)
        case "e2e_p01" | "e2e_p02":
            raise ValueError(f"{spec.algorithm} is built by ecu_simulator.car.e2e")
