"""BitField and the ported Toyota, Honda and Hyundai (classic CAN) checksum rules, checked against opendbc's frames.

The oracle is tests/fixtures/car/integrity_vectors.json: frames that opendbc's CANPacker produced. A checksum that is
wrong shows only on a real bus, so every frame in the fixture that has a rule is recomputed here and compared byte for
byte. The BitField comparison builds opendbc's Signal the way opendbc's DBC parser does, with the vendored adapter
(`ecu_simulator._opendbc`), and neither opendbc nor its dependencies are needed to run these tests.
"""

import json
import random
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from ecu_simulator._opendbc import Signal, set_value
from ecu_simulator.car.bits import BitField
from ecu_simulator.car.checksums import (
    HYUNDAI_COUNTER_SIGNALS,
    HYUNDAI_SPLIT_COUNTERS,
    RULES,
    honda_checksum,
    hyundai_can_checksum,
    is_hyundai_checksum,
    toyota_checksum,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "car" / "integrity_vectors.json"
VECTORS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
RULE_VECTORS = [vector for vector in VECTORS if vector["rule"] is not None]

# opendbc's DBC._parse_lines numbers the bits of a big-endian signal with this table: bytes in order, bits 7 down to 0.
OPENDBC_BE_BITS = [j + i * 8 for i in range(64) for j in range(7, -1, -1)]


def checksum_fields(vector: dict[str, Any]) -> list[tuple[BitField, str]]:
    return [(BitField(c["start"], c["size"], c["little_endian"]), c["signal"]) for c in vector["checksums"]]


def recompute(vector: dict[str, Any], frame_hex: str, perturb: int = 0) -> tuple[bytes, list[int]]:
    """The frame with its checksum fields recomputed, the way opendbc's packer does, and the values it wrote.

    The checksum fields start at zero and are written in DBC order, each result landing in the frame before the next
    call. `perturb` is added to every computed value before it is written.
    """
    data = bytearray.fromhex(frame_hex)
    fields = checksum_fields(vector)
    for field, _ in fields:
        field.write(data, 0)
    written = []
    for field, signal in fields:
        value = RULES[vector["rule"]](vector["address"], data, field, signal)
        written.append(value)
        field.write(data, value + perturb)
    return bytes(data), written


def vector_id(vector: dict[str, Any]) -> str:
    return f"{vector['rule']}-{vector['dbc']}-{vector['message']}-{vector['address']:#x}-v{vector['variant']}"


def opendbc_signal(start: int, size: int, little_endian: bool) -> Signal:
    """A Signal built the way opendbc's DBC._parse_lines builds one from `start|size@endian`."""
    if little_endian:
        lsb = start
        msb = start + size - 1
    else:
        idx = OPENDBC_BE_BITS.index(start)
        lsb = OPENDBC_BE_BITS[idx + size - 1]
        msb = start
    return Signal("S", start, msb, lsb, size, False, 1.0, 0.0, little_endian)


@pytest.mark.parametrize("vector", RULE_VECTORS, ids=vector_id)
def test_checksum_matches_oracle(vector: dict[str, Any]) -> None:
    for k, frame_hex in enumerate(vector["frames_hex"]):
        frame, _ = recompute(vector, frame_hex)
        assert frame.hex() == frame_hex, f"frame {k} of {vector['message']} ({vector['address']:#x})"


def test_oracle_covers_every_rule() -> None:
    """A filtering bug must not let the oracle test pass over nothing: every rule has vectors, frames and checksums."""
    vectors: Counter[str] = Counter()
    frames: Counter[str] = Counter()
    evaluations: Counter[str] = Counter()
    nonzero: Counter[str] = Counter()
    for vector in RULE_VECTORS:
        rule = vector["rule"]
        vectors[rule] += 1
        for frame_hex in vector["frames_hex"]:
            frames[rule] += 1
            _, written = recompute(vector, frame_hex)
            evaluations[rule] += len(written)
            nonzero[rule] += sum(1 for value in written if value)
    assert set(RULES) == {"toyota", "honda", "hyundai_can"}
    for rule in RULES:
        assert vectors[rule] > 0, rule
        assert frames[rule] > 0, rule
        assert evaluations[rule] > 0, rule
        assert nonzero[rule] > 0, rule
    assert sum(vectors.values()) == sum(1 for vector in VECTORS if vector["rule"] is not None)


def test_oracle_test_can_fail() -> None:
    """Recomputing with every checksum off by one must disagree with opendbc's frame, or the oracle proves nothing."""
    vector = next(v for v in RULE_VECTORS if v["rule"] == "toyota" and v["checksums"] and v["variant"] == 0)
    for frame_hex in vector["frames_hex"]:
        frame, _ = recompute(vector, frame_hex)
        assert frame.hex() == frame_hex
        wrong, _ = recompute(vector, frame_hex, perturb=1)
        assert wrong.hex() != frame_hex


def test_bitfield_round_trip_big_and_little_endian() -> None:
    # (start, size, little_endian, value, expected frame) as DBC `start|size@endian` writes them: `@1` little endian
    # (Intel), `@0` big endian (Motorola, the start bit is the most significant bit of the field).
    cases = [
        (0, 16, True, 0x1234, "3412000000000000"),
        (4, 8, True, 0xAB, "b00a000000000000"),
        (46, 2, True, 0x3, "0000000000c00000"),
        (56, 8, True, 0xFF, "00000000000000ff"),
        (7, 16, False, 0x1234, "1234000000000000"),
        (3, 8, False, 0xAB, "0ab0000000000000"),
        (61, 2, False, 0x3, "0000000000000030"),
        (59, 4, False, 0xA, "000000000000000a"),
        (0, 1, False, 1, "0100000000000000"),
    ]
    for start, size, little_endian, value, frame in cases:
        field = BitField(start, size, little_endian)
        data = bytearray(8)
        field.write(data, value)
        assert data.hex() == frame, (start, size, little_endian)
        assert field.read(data) == value, (start, size, little_endian)


def test_bitfield_write_touches_only_its_own_bits() -> None:
    for little_endian in (True, False):
        for start, size in [(3, 8), (11, 12), (23, 5), (45, 7), (60, 4)]:
            field = BitField(start, size, little_endian)
            ones = bytearray(b"\xff" * 8)
            field.write(ones, 0)
            zeros = bytearray(8)
            field.write(zeros, (1 << size) - 1)
            assert field.read(ones) == 0
            assert field.read(zeros) == (1 << size) - 1
            # What one write set, the other cleared: together they cover every bit exactly once.
            assert bytes(a ^ b for a, b in zip(ones, zeros, strict=True)) == b"\xff" * 8


def test_bitfield_write_masks_the_value_to_its_size() -> None:
    field = BitField(8, 4, True)
    data = bytearray(8)
    field.write(data, 0x1F5)
    assert data.hex() == "0005000000000000"
    assert field.read(data) == 0x5
    field.write(data, -1)
    assert field.read(data) == 0xF
    assert data.hex() == "000f000000000000"


def test_bitfield_reads_bytes_and_bytearray() -> None:
    field = BitField(7, 16, False)
    assert field.read(bytes.fromhex("1234000000000000")) == 0x1234
    assert field.read(bytearray.fromhex("1234000000000000")) == 0x1234


@pytest.mark.parametrize("start, size", [(-1, 4), (0, 0), (0, -3)])
def test_bitfield_rejects_an_empty_or_negative_field(start: int, size: int) -> None:
    for little_endian in (True, False):
        with pytest.raises(ValueError):
            BitField(start, size, little_endian)


def test_bitfield_is_a_frozen_value() -> None:
    field = BitField(46, 2, True)
    assert field == BitField(46, 2, True)
    assert hash(field) == hash(BitField(46, 2, True))
    assert field != BitField(46, 2, False)
    with pytest.raises(AttributeError):
        field.start = 3  # type: ignore[misc]


@pytest.mark.parametrize("little_endian", [True, False])
@pytest.mark.parametrize("size", [1, 2, 3, 4, 7, 8, 9, 12, 15, 16, 24, 32, 63, 64])
def test_bitfield_matches_opendbc_signal(size: int, little_endian: bool) -> None:
    """Same msb and lsb, same value read and same bytes written as opendbc's Signal, for every start bit and length."""
    rng = random.Random(size * 2 + little_endian)
    frames = {length: [rng.randbytes(length) for _ in range(4)] for length in (2, 5, 8)}
    checked = 0
    for start in range(64):
        if not little_endian and OPENDBC_BE_BITS.index(start) + size > len(OPENDBC_BE_BITS):
            continue  # opendbc's own table ends here, so it has no signal to compare with
        signal = opendbc_signal(start, size, little_endian)
        field = BitField(start, size, little_endian)
        assert (field.msb, field.lsb) == (signal.msb, signal.lsb), (start, size, little_endian)
        for length, candidates in frames.items():
            for frame in candidates:
                assert field.read(frame) == signal.get_raw_value(frame), (start, size, little_endian, length)
                value = rng.getrandbits(size + 3)  # bits above the size are masked away by both
                ours = bytearray(frame)
                theirs = bytearray(frame)
                field.write(ours, value)
                set_value(theirs, signal, value)
                assert ours == theirs, (start, size, little_endian, length, value)
                checked += 1
    assert checked > 0


def test_bitfield_fits_says_whether_every_bit_lies_inside_the_frame() -> None:
    # (start, size, little_endian, the smallest frame in bytes that holds every bit)
    cases = [
        (0, 8, True, 1),
        (4, 8, True, 2),
        (56, 8, True, 8),
        (63, 1, True, 8),
        (7, 8, False, 1),
        (7, 16, False, 2),
        (3, 8, False, 2),
        (15, 12, False, 3),
        (61, 2, False, 8),
        (0, 1, False, 1),
    ]
    for start, size, little_endian, needed in cases:
        field = BitField(start, size, little_endian)
        assert field.fits(needed), (start, size, little_endian)
        assert field.fits(needed + 1), (start, size, little_endian)
        assert not field.fits(needed - 1), (start, size, little_endian)
        # fits agrees with write: a field that does not fit loses bits that a write would have set.
        short = bytearray(needed - 1)
        field.write(short, (1 << size) - 1)
        assert field.read(short) != (1 << size) - 1


def test_bitfield_msb_lsb_for_big_endian_signals_that_cross_a_byte() -> None:
    # `7|16@0` is bits 7..0 of byte 0 then 15..8 of byte 1 in significance order; `3|8@0` ends at bit 12.
    assert (BitField(7, 16, False).msb, BitField(7, 16, False).lsb) == (7, 8)
    assert (BitField(3, 8, False).msb, BitField(3, 8, False).lsb) == (3, 12)
    assert (BitField(61, 2, False).msb, BitField(61, 2, False).lsb) == (61, 60)
    assert (BitField(46, 2, True).msb, BitField(46, 2, True).lsb) == (47, 46)


def test_rules_are_keyed_by_the_fixture_names_and_read_only() -> None:
    assert dict(RULES) == {
        "toyota": toyota_checksum,
        "honda": honda_checksum,
        "hyundai_can": hyundai_can_checksum,
    }
    assert isinstance(RULES, MappingProxyType)
    assert isinstance(HYUNDAI_SPLIT_COUNTERS, MappingProxyType)
    assert {vector["rule"] for vector in VECTORS} - {None} == set(RULES)


@pytest.mark.parametrize("rule", ["toyota", "honda", "hyundai_can"])
@pytest.mark.parametrize("address", [0x260, 0x386, 0x394, 0x1D3, 0x33DA, 0xF31AA54])
def test_checksum_functions_do_not_modify_the_frame(rule: str, address: int) -> None:
    data = bytearray(b"\x12\x34\x56\x78\x9a\xbc\xde\xf0")
    before = bytes(data)
    RULES[rule](address, data, BitField(56, 4, True), "Checksum")
    assert bytes(data) == before


def test_honda_checksum_leaves_out_the_last_nibble_of_the_frame() -> None:
    """The last nibble is where the checksum itself sits, so it never counts toward the sum; every other nibble does."""
    field = BitField(59, 4, False)
    frame = bytearray(b"\x12\x34\x56\x78\x9a\xbc\xde\xf0")
    base = honda_checksum(0x1D3, frame, field, "CHECKSUM")
    for last in (0x0, 0x5, 0xF):
        frame[7] = 0xF0 | last
        assert honda_checksum(0x1D3, frame, field, "CHECKSUM") == base
    frame[7] = 0xE0
    assert honda_checksum(0x1D3, frame, field, "CHECKSUM") == (base + 1) & 0xF
    frame[7] = 0xF0
    frame[0] ^= 0x01
    assert honda_checksum(0x1D3, frame, field, "CHECKSUM") == (base - 1) & 0xF


def test_hyundai_0x386_uses_signal_name_suffix() -> None:
    """WHL_SPD11 carries one 4-bit checksum in two 2-bit signals; the name's suffix picks which half a call returns."""
    vectors = [v for v in VECTORS if v["rule"] == "hyundai_can" and v["address"] == 0x386]
    assert {v["variant"] for v in vectors} == {0, 1, 2, 3}
    differing = frames = 0
    for vector in vectors:
        (lsb_field, lsb_name), (msb_field, msb_name) = checksum_fields(vector)
        assert (lsb_name, msb_name) == ("WHL_SPD_Checksum_LSB", "WHL_SPD_Checksum_MSB")
        for frame_hex in vector["frames_hex"]:
            frame = bytearray.fromhex(frame_hex)
            wanted_lsb, wanted_msb = lsb_field.read(frame), msb_field.read(frame)
            data = bytearray(frame)
            lsb_field.write(data, 0)
            msb_field.write(data, 0)
            # The same frame, with only the name changed.
            assert hyundai_can_checksum(0x386, data, lsb_field, lsb_name) == wanted_lsb
            assert hyundai_can_checksum(0x386, data, lsb_field, msb_name) == wanted_msb
            assert hyundai_can_checksum(0x386, data, msb_field, msb_name) == wanted_msb
            assert hyundai_can_checksum(0x386, data, msb_field, "SomethingElse") == wanted_lsb
            frames += 1
            differing += wanted_lsb != wanted_msb
    assert frames > 0
    assert differing > 0, "the two halves never differ in the fixture, so the suffix would go untested"


def test_hyundai_0x394_checksum_ignores_the_last_byte() -> None:
    (vector,) = [v for v in VECTORS if v["rule"] == "hyundai_can" and v["address"] == 0x394 and v["variant"] == 0]
    ((field, signal),) = checksum_fields(vector)
    for frame_hex in vector["frames_hex"]:
        data = bytearray.fromhex(frame_hex)
        field.write(data, 0)
        before = hyundai_can_checksum(0x394, data, field, signal)
        data[7] ^= 0xFF
        assert hyundai_can_checksum(0x394, data, field, signal) == before
        data[6] ^= 0x10
        assert hyundai_can_checksum(0x394, data, field, signal) != before


@pytest.mark.parametrize(
    "name, size, expected",
    [
        ("Checksum", 4, True),
        ("Checksum", 8, False),
        ("CheckSum_TCS3", 4, True),
        ("CR_VSM_ChkSum", 4, True),
        ("WHL_SPD_Checksum_LSB", 2, True),
        ("WHL_SPD_Checksum_MSB", 2, True),
        ("WHL_SPD_Checksum_", 2, True),
        ("WHL_SPD_AliveCounter_LSB", 2, False),
        ("CHECKSUM", 4, False),
        ("AliveCounter", 4, False),
        ("", 4, False),
    ],
)
def test_is_hyundai_checksum(name: str, size: int, expected: bool) -> None:
    assert is_hyundai_checksum(name, size) is expected


def test_hyundai_name_sets_agree_with_the_fixture() -> None:
    assert HYUNDAI_COUNTER_SIGNALS == {"AliveCounter", "AliveCounterTCS", "CR_VSM_Alive", "CF_Clu_AliveCnt1"}
    assert dict(HYUNDAI_SPLIT_COUNTERS) == {"WHL_SPD_AliveCounter_LSB": 0, "WHL_SPD_AliveCounter_MSB": 2}
    hyundai = [v for v in VECTORS if v["rule"] == "hyundai_can"]
    assert hyundai
    for vector in hyundai:
        for checksum in vector["checksums"]:
            assert is_hyundai_checksum(checksum["signal"], checksum["size"]), checksum
        for counter in vector["counters"]:
            name = counter["signal"]
            assert name in HYUNDAI_COUNTER_SIGNALS | set(HYUNDAI_SPLIT_COUNTERS), counter
            assert HYUNDAI_SPLIT_COUNTERS.get(name, 0) == counter["shift"], counter
