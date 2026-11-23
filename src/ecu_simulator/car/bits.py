"""A DBC bit field: where a signal's bits sit in a CAN frame, and how to read and write them.

Reads and writes follow comma.ai opendbc's ``Signal.get_raw_value`` (``opendbc/can/dbc.py``) and ``set_value``
(``opendbc/can/packer.py``), and ``msb`` and ``lsb`` are derived the way ``DBC._parse_lines`` derives them, at upstream
revision af54130d51111197d34384e7dad9ace229b724e4. opendbc is MIT licensed; the notice is in ``_opendbc/LICENSE``.

The checksum rules in ``ecu_simulator.car.checksums`` read the field they are about to fill, so this module must give
exactly the bytes opendbc gives. It imports nothing from the package.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BitField:
    """A signal's position as a DBC writes it, ``start|size@endian``.

    ``little_endian`` is the ``@1`` (Intel) layout, where ``start`` is the least significant bit and the value grows
    toward higher bit numbers. ``@0`` (Motorola) is big endian: ``start`` is the most significant bit, and the bits
    that follow it run down through each byte (7 to 0) and on into the next byte. Bit ``n`` is bit ``n % 8`` of byte
    ``n // 8``. ``msb`` and ``lsb`` are the message bit numbers of the field's most and least significant bit.

    Bits that fall outside the frame handed to ``read`` or ``write`` are skipped, as opendbc skips them.
    """

    start: int
    size: int
    little_endian: bool
    msb: int = field(init=False, repr=False, compare=False)
    lsb: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.start < 0 or self.size < 1:
            raise ValueError(
                f"a bit field needs a start of 0 or more and a size of 1 or more, got {self.start}|{self.size}"
            )
        if self.little_endian:
            msb = self.start + self.size - 1
            lsb = self.start
        else:
            # opendbc numbers big-endian bits with a table that lists each byte from bit 7 down to bit 0, so a bit's
            # place in that table is its number with the low three bits flipped. `x ^ 7` converts either way.
            msb = self.start
            lsb = ((self.start ^ 7) + self.size - 1) ^ 7
        object.__setattr__(self, "msb", msb)
        object.__setattr__(self, "lsb", lsb)

    def fits(self, size_bytes: int) -> bool:
        """Whether every bit of the field lies inside a frame of ``size_bytes`` bytes."""
        return max(self.msb, self.lsb) // 8 < size_bytes

    def read(self, data: bytes | bytearray) -> int:
        """The field's value in ``data``."""
        value = 0
        i = self.msb // 8
        bits = self.size
        while 0 <= i < len(data) and bits > 0:
            lsb = self.lsb if self.lsb // 8 == i else i * 8
            msb = self.msb if self.msb // 8 == i else (i + 1) * 8 - 1
            size = msb - lsb + 1
            value |= ((data[i] >> (lsb - i * 8)) & ((1 << size) - 1)) << (bits - size)
            bits -= size
            i = i - 1 if self.little_endian else i + 1
        return value

    def write(self, data: bytearray, value: int) -> None:
        """Set the field in ``data`` to ``value``, masked to ``size`` bits, and leave every other bit alone."""
        i = self.lsb // 8
        bits = self.size
        while 0 <= i < len(data) and bits > 0:
            shift = self.lsb % 8 if self.lsb // 8 == i else 0
            size = min(bits, 8 - shift)
            chunk = (1 << size) - 1
            data[i] = (data[i] & ~(chunk << shift)) | ((value & chunk) << shift)
            bits -= size
            value >>= size
            i = i + 1 if self.little_endian else i - 1
