"""The generic checksum algorithms a rule file can ask for, checked against published values and hand arithmetic.

The CRC cases are the catalogue check values (Greg Cook's CRC RevEng catalogue): the CRC of the ASCII string
``123456789`` under each named parameter set. They run through ``build_checksum`` on a real frame, the nine characters
followed by the checksum field, so the covered-byte selection, the algorithm and the result mask are all exercised.
The xor, sum and nibble-sum values below are worked out by hand in the comments, not by calling the implementation
twice.
"""

from typing import Any

import pytest

from ecu_simulator.car.algorithms import build_checksum
from ecu_simulator.car.bits import BitField
from ecu_simulator.car.rules import ChecksumSpec

CHECK_STRING = b"123456789"


def _spec(**fields: Any) -> ChecksumSpec:
    return ChecksumSpec.model_validate({"signal": "CHK", **fields})


def _run(spec: ChecksumSpec, payload: bytes, field_bits: int, *, field: BitField | None = None) -> int:
    """The checksum of ``payload`` followed by a zeroed checksum field of ``field_bits`` bits, as the Stamper calls it."""
    frame = bytearray(payload) + bytearray((field_bits + 7) // 8)
    field = field or BitField(len(payload) * 8, field_bits, True)
    return build_checksum(spec, field_bits)(0x123, frame, field, "CHK")


CRC_CHECK_VALUES = [
    # (catalogue name, parameters, check value over "123456789")
    ("CRC-8/SAE-J1850", {"algorithm": "crc8", "poly": 0x1D, "init": 0xFF, "xorout": 0xFF}, 0x4B),
    ("CRC-8/AUTOSAR", {"algorithm": "crc8", "poly": 0x2F, "init": 0xFF, "xorout": 0xFF}, 0xDF),
    ("CRC-16/XMODEM", {"algorithm": "crc16", "poly": 0x1021, "init": 0}, 0x31C3),
    ("CRC-16/ARC", {"algorithm": "crc16", "poly": 0x8005, "init": 0, "reflect": True}, 0xBB3D),
    ("CRC-8/SMBUS", {"algorithm": "crc8", "poly": 0x07, "init": 0}, 0xF4),
    ("CRC-16/CCITT-FALSE", {"algorithm": "crc16", "poly": 0x1021, "init": 0xFFFF}, 0x29B1),
    # Reflected, with a non-zero init and with an xorout: the input and the output are reflected, init and xorout are not.
    ("CRC-8/MAXIM-DOW", {"algorithm": "crc8", "poly": 0x31, "init": 0, "reflect": True}, 0xA1),
    ("CRC-16/MODBUS", {"algorithm": "crc16", "poly": 0x8005, "init": 0xFFFF, "reflect": True}, 0x4B37),
    ("CRC-16/X-25", {"algorithm": "crc16", "poly": 0x1021, "init": 0xFFFF, "xorout": 0xFFFF, "reflect": True}, 0x906E),
]


@pytest.mark.parametrize(("name", "params", "expected"), CRC_CHECK_VALUES, ids=[c[0] for c in CRC_CHECK_VALUES])
def test_crc_check_values_over_123456789(name: str, params: dict[str, Any], expected: int) -> None:
    bits = 8 if params["algorithm"] == "crc8" else 16
    spec = _spec(covers=list(range(9)), **params)
    assert _run(spec, CHECK_STRING, bits) == expected, name
    # With no covers listed, every byte outside the checksum field is covered: the same nine bytes here.
    assert _run(_spec(**params), CHECK_STRING, bits) == expected, name


def test_crc_ignores_the_address() -> None:
    spec = _spec(algorithm="crc8", poly=0x07, covers=list(range(9)))
    fn = build_checksum(spec, 8)
    field = BitField(72, 8, True)
    results = {fn(address, bytearray(CHECK_STRING + b"\x00"), field, "CHK") for address in (0, 0x123, 0x1FFFFFFF)}
    assert results == {0xF4}


def test_xor_sum8_nibble_sum_known_values() -> None:
    payload = bytes([0x12, 0x34, 0x56])
    # xor: 0x12 ^ 0x34 = 0x26, 0x26 ^ 0x56 = 0x70; init 0x0F gives 0x7F; xorout 0xF0 then gives 0x8F.
    assert _run(_spec(algorithm="xor"), payload, 8) == 0x70
    assert _run(_spec(algorithm="xor", init=0x0F), payload, 8) == 0x7F
    assert _run(_spec(algorithm="xor", init=0x0F, xorout=0xF0), payload, 8) == 0x8F

    payload = bytes([0xF0, 0x20, 0x05])
    # sum8: 0xF0 + 0x20 + 0x05 = 0x115, which wraps to 0x15; init 0x10 gives 0x125, wrapping to 0x25; xorout 0xFF gives 0xDA.
    assert _run(_spec(algorithm="sum8"), payload, 8) == 0x15
    assert _run(_spec(algorithm="sum8", init=0x10), payload, 8) == 0x25
    assert _run(_spec(algorithm="sum8", init=0x10, xorout=0xFF), payload, 8) == 0xDA

    payload = bytes([0x12, 0x34])
    # nibble_sum: the nibbles 1, 2, 3, 4 add to 10, and (0 - 10) mod 16 is 6. init 3 gives (3 - 10) mod 16 = 9;
    # xorout 0xF then gives 6.
    assert _run(_spec(algorithm="nibble_sum"), payload, 4) == 6
    assert _run(_spec(algorithm="nibble_sum", init=3), payload, 4) == 9
    assert _run(_spec(algorithm="nibble_sum", init=3, xorout=0xF), payload, 4) == 6


def test_data_id_is_appended_before_the_crc() -> None:
    # CRC-8/SMBUS over "123456789" is 0xF4, so "12345678" followed by the data id 0x39 ("9") must give 0xF4 as well.
    spec = _spec(algorithm="crc8", poly=0x07, covers=list(range(8)), data_id=0x39)
    assert _run(spec, CHECK_STRING[:8], 8) == 0xF4
    assert _run(_spec(algorithm="crc8", poly=0x07, covers=list(range(8))), CHECK_STRING[:8], 8) != 0xF4
    # For sum8 and xor the data id is one more byte: 1 + 2 + 3 + 0x10 = 0x16 and 1 ^ 2 ^ 3 ^ 0x10 = 0x10.
    assert _run(_spec(algorithm="sum8", data_id=0x10), bytes([1, 2, 3]), 8) == 0x16
    assert _run(_spec(algorithm="xor", data_id=0x10), bytes([1, 2, 3]), 8) == 0x10
    # A data id of zero is still a byte: it changes a CRC that has a non-zero init.
    zero = _run(_spec(algorithm="crc8", poly=0x07, init=0xFF, data_id=0), bytes([1]), 8)
    none = _run(_spec(algorithm="crc8", poly=0x07, init=0xFF), bytes([1]), 8)
    assert zero != none


def test_covers_are_taken_in_the_order_given() -> None:
    # CRC-8/SMBUS: crc([0x01]) = 0x07; crc([0x01, 0x02]) = table[0x07 ^ 0x02] = table[0x05] = 0x1B;
    # crc([0x02, 0x01]) = table[table[0x02] ^ 0x01] = table[0x0E ^ 0x01] = table[0x0F] = 0x2D.
    payload = bytes([0x01, 0x02])
    assert _run(_spec(algorithm="crc8", poly=7, covers=[0, 1]), payload, 8) == 0x1B
    assert _run(_spec(algorithm="crc8", poly=7, covers=[1, 0]), payload, 8) == 0x2D
    # Only the listed bytes count: byte 1 is left out here.
    assert _run(_spec(algorithm="crc8", poly=7, covers=[0]), payload, 8) == 0x07


def test_default_covers_exclude_the_checksum_field() -> None:
    # An 8-byte frame whose checksum is byte 3; the byte holds 0xFF here to show it is left out of the sum.
    frame = bytearray([0x01, 0x02, 0x04, 0xFF, 0x08, 0x10, 0x20, 0x40])
    fn = build_checksum(_spec(algorithm="sum8"), 8)
    assert fn(0x1, frame, BitField(24, 8, True), "CHK") == 0x7F  # 1 + 2 + 4 + 8 + 16 + 32 + 64

    # A 12-bit Intel field starting at bit 4 occupies bytes 0 and 1 (bits 4 to 15): both are left out.
    frame = bytearray([0xFF, 0xFF, 0x03, 0x04])
    assert build_checksum(_spec(algorithm="sum8"), 12)(0x1, frame, BitField(4, 12, True), "CHK") == 0x07

    # A 12-bit Motorola field starting at bit 15 runs from byte 1 into byte 2: bytes 1 and 2 are left out.
    motorola = BitField(15, 12, False)
    assert (motorola.msb // 8, motorola.lsb // 8) == (1, 2)
    frame = bytearray([0x01, 0xFF, 0xFF, 0x08])
    assert build_checksum(_spec(algorithm="sum8"), 12)(0x1, frame, motorola, "CHK") == 0x09


def test_explicit_covers_may_include_the_checksum_bytes_and_see_them_as_zero() -> None:
    # The caller zeroes the checksum field first; an explicit list is taken as written.
    frame = bytearray([0x01, 0x00, 0x04])
    fn = build_checksum(_spec(algorithm="sum8", covers=[0, 1, 2]), 8)
    assert fn(0x1, frame, BitField(8, 8, True), "CHK") == 0x05


def test_result_is_masked_to_the_field() -> None:
    # sum8 of 0x0F + 0x0F + 0x01 is 0x1F; a 4-bit field keeps the low nibble.
    assert _run(_spec(algorithm="sum8"), bytes([0x0F, 0x0F, 0x01]), 4, field=BitField(24, 4, True)) == 0xF
    # CRC-16/XMODEM is 0x31C3; an 8-bit field keeps the low byte, a 12-bit field the low three nibbles.
    xmodem = {"algorithm": "crc16", "poly": 0x1021, "covers": list(range(9))}
    assert _run(_spec(**xmodem), CHECK_STRING, 8, field=BitField(72, 8, True)) == 0xC3
    assert _run(_spec(**xmodem), CHECK_STRING, 12, field=BitField(72, 12, True)) == 0x1C3
    # An xorout the field is too narrow for is masked like everything else: 0x15 ^ 0xFF = 0xEA, whose low nibble is 0xA.
    assert _run(_spec(algorithm="sum8", xorout=0xFF), bytes([0xF0, 0x20, 0x05]), 4, field=BitField(24, 4, True)) == 0xA


def test_the_built_function_does_not_touch_the_frame() -> None:
    frame = bytearray(b"123456789\x00")
    before = bytes(frame)
    build_checksum(_spec(algorithm="crc8", poly=7), 8)(0x1, frame, BitField(72, 8, True), "CHK")
    assert bytes(frame) == before


def test_the_built_function_is_independent_per_spec() -> None:
    payload = bytes([0x01, 0x02, 0x03])
    first = build_checksum(_spec(algorithm="sum8", init=1), 8)
    second = build_checksum(_spec(algorithm="sum8", init=2), 8)
    field = BitField(24, 8, True)
    assert first(0, bytearray(payload + b"\x00"), field, "CHK") == 7
    assert second(0, bytearray(payload + b"\x00"), field, "CHK") == 8
    assert first(0, bytearray(payload + b"\x00"), field, "CHK") == 7


def test_a_field_size_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="field size"):
        build_checksum(_spec(algorithm="xor"), 0)
