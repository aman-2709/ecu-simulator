"""The Toyota, Honda and Hyundai (classic CAN) checksum rules, ported from comma.ai opendbc.

Ported at upstream revision af54130d51111197d34384e7dad9ace229b724e4 from ``opendbc/car/toyota/toyotacan.py``
(``toyota_checksum``), ``opendbc/car/honda/hondacan.py`` (``honda_checksum``) and ``opendbc/car/hyundai/hyundaican.py``
(``hyundai_can_checksum``); the Hyundai signal-name sets come from ``hyundai_setup_signal`` in ``opendbc/can/dbc.py``.
opendbc is MIT licensed; the notice is in ``_opendbc/LICENSE``.

Upstream passes each function ``(address, sig, d)`` with ``sig`` its ``Signal``. These take ``(address, data, field,
signal_name)`` and read from ``field`` and ``signal_name`` whatever upstream reads from ``sig``: the signal's bits and
its name. ``data`` is the frame with the checksum field still zero, as opendbc's packer has it when it calls the rule.
When a message has several checksum fields the caller writes each result into ``data`` before computing the next.
"""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from ecu_simulator.car.bits import BitField

ChecksumFn = Callable[[int, bytearray, BitField, str], int]


def toyota_checksum(address: int, data: bytearray, field: BitField, signal_name: str) -> int:
    total = len(data)
    addr = address
    while addr:
        total += addr & 0xFF
        addr >>= 8
    for i in range(len(data) - 1):
        total += data[i]
    return total & 0xFF


def honda_checksum(address: int, data: bytearray, field: BitField, signal_name: str) -> int:
    total = 0
    extended = address > 0x7FF
    addr = address
    while addr:
        total += addr & 0xF
        addr >>= 4
    for i in range(len(data)):
        x = data[i]
        if i == len(data) - 1:
            x >>= 4
        total += (x & 0xF) + (x >> 4)
    total = 8 - total
    if extended:
        total += 3
    return total & 0xF


def hyundai_can_checksum(address: int, data: bytearray, field: BitField, signal_name: str) -> int:
    if address == 0x386:
        checksum = sum((b & (0x3F if i % 2 else 0xFF)).bit_count() for i, b in enumerate(data)) ^ 9
        return (checksum >> (2 if signal_name.endswith("_MSB") else 0)) & 0x3

    if address == 0x394:
        data = data[:7]
    return (field.read(data) - sum((b >> 4) + (b & 0xF) for b in data)) & 0xF


RULES: Mapping[str, ChecksumFn] = MappingProxyType(
    {
        "toyota": toyota_checksum,
        "honda": honda_checksum,
        "hyundai_can": hyundai_can_checksum,
    }
)

# opendbc finds the Hyundai signals by name, because hyundai_can_generated.dbc marks none of them.
HYUNDAI_COUNTER_SIGNALS = frozenset({"AliveCounter", "AliveCounterTCS", "CR_VSM_Alive", "CF_Clu_AliveCnt1"})

# WHL_SPD11 keeps one 4-bit counter in two 2-bit signals; the value is the sum of each signal shifted by its entry.
HYUNDAI_SPLIT_COUNTERS: Mapping[str, int] = MappingProxyType(
    {"WHL_SPD_AliveCounter_LSB": 0, "WHL_SPD_AliveCounter_MSB": 2}
)


def is_hyundai_checksum(name: str, size: int) -> bool:
    """Whether opendbc treats a signal of this name and width in hyundai_can_generated.dbc as a checksum."""
    return (
        (name == "Checksum" and size == 4)
        or name in ("CheckSum_TCS3", "CR_VSM_ChkSum")
        or name.startswith("WHL_SPD_Checksum_")
    )
