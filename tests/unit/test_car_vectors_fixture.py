"""The committed oracle vectors: frames that opendbc's CANPacker produced, for the integrity engine to replay.

tests/fixtures/car/integrity_vectors.json is written by scripts/gen_integrity_vectors.py against a pinned opendbc
checkout. These tests read only the committed file and the shipped catalog, so they need neither opendbc nor its
dependencies. Field values are decoded here with a small bit reader of its own rather than with any production code,
so the layouts in the fixture are checked against the frames, not against themselves.
"""

import json
import re
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator.car.catalog import load_catalog

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "car" / "integrity_vectors.json"
MAX_FIXTURE_BYTES = 1024 * 1024
EXTENDED_FLAG = 0x80000000
VECTOR_KEYS = {
    "rule",
    "dbc",
    "dbcs",
    "address",
    "extended",
    "message",
    "size",
    "base_hex",
    "counters",
    "checksums",
    "frames_hex",
}
COUNTER_KEYS = {"signal", "start", "size", "little_endian", "shift", "max"}
CHECKSUM_KEYS = {"signal", "start", "size", "little_endian"}


@pytest.fixture(scope="module")
def vectors() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def bit_positions(field: dict[str, Any]) -> list[int]:
    """The message bit numbers a DBC field covers, most significant bit first (bit 8 * byte + position in byte)."""
    start, size = field["start"], field["size"]
    if field["little_endian"]:
        return list(range(start + size - 1, start - 1, -1))
    bits = []
    bit = start
    for _ in range(size):
        bits.append(bit)
        bit = bit + 15 if bit % 8 == 0 else bit - 1
    return bits


def read_field(frame_hex: str, field: dict[str, Any]) -> int:
    frame = int.from_bytes(bytes.fromhex(frame_hex), "little")
    value = 0
    for bit in bit_positions(field):
        value = (value << 1) | ((frame >> bit) & 1)
    return value


def field_mask(vector: dict[str, Any]) -> int:
    mask = 0
    for field in [*vector["counters"], *vector["checksums"]]:
        for bit in bit_positions(field):
            mask |= 1 << bit
    return mask


def counter_period(vector: dict[str, Any]) -> int:
    return vector["counters"][0]["max"] + 1 if vector["counters"] else 1


def layout(vector: dict[str, Any]) -> tuple[Any, ...]:
    return (
        vector["rule"],
        vector["address"],
        vector["size"],
        json.dumps(vector["counters"], sort_keys=True),
        json.dumps(vector["checksums"], sort_keys=True),
    )


def test_fixture_is_a_nonempty_list_of_vectors_under_one_mebibyte(vectors: list[dict[str, Any]]) -> None:
    assert isinstance(vectors, list)
    assert vectors
    assert FIXTURE.stat().st_size <= MAX_FIXTURE_BYTES


def test_fixture_covers_each_rule(vectors: list[dict[str, Any]]) -> None:
    assert {"toyota", "honda", "hyundai_can"} <= {vector["rule"] for vector in vectors}


def test_hyundai_split_counter_present(vectors: list[dict[str, Any]]) -> None:
    (vector,) = [v for v in vectors if v["rule"] == "hyundai_can" and v["address"] == 0x386]
    assert vector["message"] == "WHL_SPD11"
    assert [(c["signal"], c["shift"], c["max"]) for c in vector["counters"]] == [
        ("WHL_SPD_AliveCounter_LSB", 0, 15),
        ("WHL_SPD_AliveCounter_MSB", 2, 15),
    ]
    assert [(c["signal"], c["start"], c["size"]) for c in vector["checksums"]] == [
        ("WHL_SPD_Checksum_LSB", 46, 2),
        ("WHL_SPD_Checksum_MSB", 62, 2),
    ]
    assert len(vector["frames_hex"]) == 15 + 3


def test_vectors_have_exactly_the_documented_fields(vectors: list[dict[str, Any]]) -> None:
    for vector in vectors:
        assert set(vector) == VECTOR_KEYS, vector["message"]
        assert all(set(c) == COUNTER_KEYS for c in vector["counters"]), vector["message"]
        assert all(set(c) == CHECKSUM_KEYS for c in vector["checksums"]), vector["message"]
        assert vector["rule"] in {"toyota", "honda", "hyundai_can", None}
        assert vector["dbc"] == vector["dbcs"][0]
        assert vector["dbcs"] == sorted(vector["dbcs"])
        assert vector["extended"] == (vector["address"] > 0x7FF)
        assert vector["counters"] or vector["checksums"], f"{vector['message']} has no integrity field"
        assert all(isinstance(c["little_endian"], bool) for c in [*vector["counters"], *vector["checksums"]])


def test_each_vector_wraps_the_counter(vectors: list[dict[str, Any]]) -> None:
    for vector in vectors:
        frames = vector["frames_hex"]
        period = counter_period(vector)
        assert len(frames) == (vector["counters"][0]["max"] if vector["counters"] else 0) + 3, vector["message"]
        for k in range(period, len(frames)):
            assert frames[k] == frames[k - period], f"{vector['message']} frame {k} does not repeat frame {k - period}"


def test_every_hex_string_is_one_message_long(vectors: list[dict[str, Any]]) -> None:
    for vector in vectors:
        for text in [vector["base_hex"], *vector["frames_hex"]]:
            assert re.fullmatch(r"(?:[0-9a-f]{2})+", text), vector["message"]
            assert len(text) == 2 * vector["size"], vector["message"]


def test_base_has_every_counter_and_checksum_field_zero(vectors: list[dict[str, Any]]) -> None:
    for vector in vectors:
        for field in [*vector["counters"], *vector["checksums"]]:
            assert read_field(vector["base_hex"], field) == 0, f"{vector['message']} {field['signal']}"


def test_frames_differ_from_base_only_in_counter_and_checksum_fields(vectors: list[dict[str, Any]]) -> None:
    for vector in vectors:
        base = int.from_bytes(bytes.fromhex(vector["base_hex"]), "little")
        keep = ~field_mask(vector)
        for k, frame_hex in enumerate(vector["frames_hex"]):
            frame = int.from_bytes(bytes.fromhex(frame_hex), "little")
            assert (frame ^ base) & keep == 0, f"{vector['message']} frame {k}"


def test_counter_fields_hold_the_call_index_modulo_the_period(vectors: list[dict[str, Any]]) -> None:
    for vector in vectors:
        period = counter_period(vector)
        for k, frame_hex in enumerate(vector["frames_hex"]):
            value = sum(read_field(frame_hex, c) << c["shift"] for c in vector["counters"])
            assert value == k % period, f"{vector['message']} frame {k}"


def test_checksums_are_filled_in(vectors: list[dict[str, Any]]) -> None:
    for vector in vectors:
        fields = vector["checksums"]
        if fields:
            seen = {read_field(frame_hex, field) for frame_hex in vector["frames_hex"] for field in fields}
            assert seen != {0}, f"{vector['message']} has a zero checksum in every frame"


def test_vectors_deduplicated_by_layout(vectors: list[dict[str, Any]]) -> None:
    repeated = [key for key, count in Counter(layout(vector) for vector in vectors).items() if count > 1]
    assert not repeated


def test_vectors_follow_the_shipped_catalog(vectors: list[dict[str, Any]]) -> None:
    dbcs = load_catalog().dbcs
    for vector in vectors:
        for name in vector["dbcs"]:
            assert name in dbcs, name
            assert dbcs[name].rule == vector["rule"], f"{name} is labelled {dbcs[name].rule}, not {vector['rule']}"


def test_layouts_agree_with_the_shipped_dbc_files(vectors: list[dict[str, Any]]) -> None:
    """The fixture came from opendbc's reading of its source files; the wheel ships normalised copies of them."""
    root = Path(str(files("ecu_simulator._opendbc")))
    catalog = load_catalog()
    for vector in vectors:
        wire_id = vector["address"] | (EXTENDED_FLAG if vector["extended"] else 0)
        for name in vector["dbcs"]:
            text = (root / catalog.dbcs[name].file).read_text(encoding="utf-8")
            block = re.search(rf"^BO_ {wire_id} (\w+) *: (\d+) .*(?:\n[ \t]+SG_ .*)*", text, re.M)
            assert block, f"{name} has no message {wire_id}"
            assert int(block[2]) == vector["size"], name
            if name == vector["dbc"]:
                # Another DBC may name the same address and layout differently, so only the first is checked.
                assert block[1] == vector["message"], name
            for field in [*vector["counters"], *vector["checksums"]]:
                endian = 1 if field["little_endian"] else 0
                pattern = rf"^[ \t]+SG_ {field['signal']} : {field['start']}\|{field['size']}@{endian}[+-] "
                assert re.search(pattern, block[0], re.M), f"{name} {vector['message']} {field['signal']}"
