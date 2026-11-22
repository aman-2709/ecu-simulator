"""Plain unsigned-signal subset of comma.ai opendbc's CANPacker and Signal.

Upstream revision 79ddcc7bb72d557fc5b20d038c0fad9c9d423446, see LICENSE.
The packing loop, set_value and get_raw_value come from opendbc/can/{packer,dbc}.py.
This adapter takes resolved signals and omits the unrelated counter and checksum paths.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass
class Signal:
    name: str
    start_bit: int
    msb: int
    lsb: int
    size: int
    is_signed: bool
    factor: float
    offset: float
    is_little_endian: bool

    def get_raw_value(self, dat: bytes | bytearray) -> int:
        ret = 0
        i = self.msb // 8
        bits = self.size
        while 0 <= i < len(dat) and bits > 0:
            lsb = self.lsb if (self.lsb // 8) == i else i * 8
            msb = self.msb if (self.msb // 8) == i else (i + 1) * 8 - 1
            size = msb - lsb + 1
            d = (dat[i] >> (lsb - (i * 8))) & ((1 << size) - 1)
            ret |= d << (bits - size)
            bits -= size
            i = i - 1 if self.is_little_endian else i + 1
        return ret


def pack(size: int, signals: Mapping[str, Signal], values: Mapping[str, float]) -> bytes:
    dat = bytearray(size)
    for name, value in values.items():
        sig = signals[name]
        ival = int(math.floor((value - sig.offset) / sig.factor + 0.5))
        if ival < 0:
            ival = (1 << sig.size) + ival
        set_value(dat, sig, ival)
    return bytes(dat)


def set_value(msg: bytearray, sig: Signal, ival: int) -> None:
    i = sig.lsb // 8
    bits = sig.size
    if sig.size < 64:
        ival &= (1 << sig.size) - 1
    while 0 <= i < len(msg) and bits > 0:
        shift = sig.lsb % 8 if (sig.lsb // 8) == i else 0
        size = min(bits, 8 - shift)
        mask = ((1 << size) - 1) << shift
        msg[i] &= ~mask
        msg[i] |= (ival & ((1 << size) - 1)) << shift
        bits -= size
        ival >>= size
        i = i + 1 if sig.is_little_endian else i - 1
