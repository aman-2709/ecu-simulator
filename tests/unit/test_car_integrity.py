"""The integrity engine: which fields of a DBC message are counters and checksums, and frames stamped with them.

The oracle is tests/fixtures/car/integrity_vectors.json, frames that opendbc's CANPacker produced. Each vector's
message is loaded from its catalog DBC through ImportedDb, planned, and stamped call after call from the frame with
every counter and checksum field zero; each stamped frame must equal the packer's byte for byte.
"""

import dataclasses
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator.car.algorithms import build_checksum
from ecu_simulator.car.bits import BitField
from ecu_simulator.car.catalog import load_catalog
from ecu_simulator.car.checksums import honda_checksum, toyota_checksum
from ecu_simulator.car.integrity import (
    ChecksumField,
    CounterField,
    IntegrityState,
    MessagePlan,
    Stamper,
    plan_message,
)
from ecu_simulator.car.rules import ChecksumSpec, UserRule
from ecu_simulator.dbc import DbcStore, ImportedDb

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "car" / "integrity_vectors.json"
VECTORS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))

# The messages whose catalog DBC has a layout ImportedDb refuses; every variant of each is a vector.
LAYOUT_ERRORS = {
    ("acura_ilx_2016_can_generated", "KINEMATICS"),
    ("acura_ilx_2016_can_generated", "KINEMATICS_ALT"),
    ("acura_ilx_2016_can_generated", "BRAKE_PRESSURE"),
    ("acura_rdx_2020_can_generated", "BRAKE_HOLD"),
}

POSSIBLE = "possible integrity field held at its initial value"


def _import(tmp_path: Path, raw: bytes) -> ImportedDb:
    store = DbcStore(tmp_path)
    return store.load(store.import_file("synthetic.dbc", raw)["sha256"])


def _catalog_messages(tmp_path: Path, name: str) -> dict[tuple[int, bool], dict[str, Any]]:
    db = DbcStore(tmp_path).load(load_catalog().dbcs[name].sha256)
    return {(m["id"], m["extended"]): m for m in db.catalog(name)["messages"]}


def _layout(field: BitField) -> tuple[int, int, bool]:
    return field.start, field.size, field.little_endian


def test_stamper_matches_oracle_for_every_vector(tmp_path: Path) -> None:
    loaded: dict[str, dict[tuple[int, bool], dict[str, Any]]] = {}
    compared: Counter[str | None] = Counter()
    frames: Counter[str | None] = Counter()
    unsupported = 0
    for vector in VECTORS:
        name = vector["dbc"]
        if name not in loaded:
            loaded[name] = _catalog_messages(tmp_path, name)
        message = loaded[name][vector["address"], vector["extended"]]
        assert message["name"] == vector["message"]
        plan = plan_message(message, rule=vector["rule"])
        if (name, vector["message"]) in LAYOUT_ERRORS:
            assert plan.state == IntegrityState.UNSUPPORTED, (name, vector["message"])
            assert plan.counters == () and plan.checksums == ()
            unsupported += 1
            continue
        where = (name, vector["message"], vector["variant"])
        assert (plan.address, plan.extended, plan.size) == (vector["address"], vector["extended"], vector["size"])
        assert [(c.signal, *_layout(c.field), c.shift, c.max_value) for c in plan.counters] == [
            (c["signal"], c["start"], c["size"], c["little_endian"], c["shift"], c["max"]) for c in vector["counters"]
        ], where
        assert [(c.signal, *_layout(c.field)) for c in plan.checksums] == [
            (c["signal"], c["start"], c["size"], c["little_endian"]) for c in vector["checksums"]
        ], where
        if vector["rule"] is not None:
            assert plan.state == IntegrityState.VALID, where
        stamper = Stamper(plan)
        base = bytes.fromhex(vector["base_hex"])
        for k, expected in enumerate(vector["frames_hex"]):
            assert stamper.stamp(base).hex() == expected, (*where, k)
            frames[vector["rule"]] += 1
        compared[vector["rule"]] += 1
    assert unsupported == 16
    assert sum(compared.values()) == len(VECTORS) - 16 == 724
    assert set(compared) == {"toyota", "honda", "hyundai_can", None}
    assert sum(frames.values()) > 3 * sum(compared.values())


def test_hyundai_split_counter_0x386(tmp_path: Path) -> None:
    message = _catalog_messages(tmp_path, "hyundai_can_generated")[0x386, False]
    plan = plan_message(message, rule="hyundai_can")
    assert plan.state == IntegrityState.VALID
    assert [(c.signal, c.shift, c.max_value) for c in plan.counters] == [
        ("WHL_SPD_AliveCounter_LSB", 0, 15),
        ("WHL_SPD_AliveCounter_MSB", 2, 15),
    ]
    assert [c.signal for c in plan.checksums] == ["WHL_SPD_Checksum_LSB", "WHL_SPD_Checksum_MSB"]
    stamper = Stamper(plan)
    lsb, msb = (c.field for c in plan.counters)
    for k in range(20):
        frame = stamper.stamp(bytes(8))
        assert lsb.read(frame) + (msb.read(frame) << 2) == k % 16


SIMPLE = b"""BO_ 257 Climate: 4 XXX
 SG_ Mode : 0|4@1+ (1,0) [0|15] "" XXX
 SG_ Voltage : 24|8@1+ (0.05,6) [0|0] "V" XXX
"""


def test_message_without_integrity_fields_is_identity(tmp_path: Path) -> None:
    db = _import(tmp_path, SIMPLE)
    for rule in ("toyota", "honda", "hyundai_can", None):
        plan = plan_message(db.message("Climate"), rule=rule)
        assert plan.state == IntegrityState.NONE
        assert plan.detail is None and plan.possible_fields == ()
        assert plan.counters == () and plan.checksums == ()
        data = bytes.fromhex("0a0b0c0d")
        stamper = Stamper(plan)
        assert stamper.stamp(data) == data
        assert stamper.stamp(data) == data


NO_RULE = b"""BO_ 300 Both: 8 XXX
 SG_ DATA : 7|8@0+ (1,0) [0|255] "" XXX
 SG_ COUNTER : 53|2@0+ (1,0) [0|3] "" XXX
 SG_ CHECKSUM : 63|8@0+ (1,0) [0|255] "" XXX
BO_ 301 ChecksumOnly: 8 XXX
 SG_ CHECKSUM : 63|8@0+ (1,0) [0|255] "" XXX
BO_ 302 Alive: 8 XXX
 SG_ AliveCounter : 0|4@1+ (1,0) [0|15] "" XXX
"""


def test_no_rule_leaves_checksum_zero_and_is_labelled_counter_only(tmp_path: Path) -> None:
    db = _import(tmp_path, NO_RULE)
    plan = plan_message(db.message("Both"), rule=None)
    assert plan.state == IntegrityState.COUNTER_ONLY
    assert plan.detail == "checksum not computed"
    assert plan.checksums == () and plan.possible_fields == ()
    assert [c.signal for c in plan.counters] == ["COUNTER"]
    checksum = BitField(63, 8, False)
    counter = BitField(53, 2, False)
    stamper = Stamper(plan)
    payload = db.encode("Both", {"DATA": 0x5A}, integrity_ok=True)
    for k in range(6):
        frame = stamper.stamp(payload)
        assert counter.read(frame) == k % 4
        assert checksum.read(frame) == 0
        assert frame[0] == 0x5A

    only = plan_message(db.message("ChecksumOnly"), rule=None)
    assert (only.state, only.detail, only.counters, only.checksums) == (
        IntegrityState.NONE,
        "checksum not computed",
        (),
        (),
    )

    alive = plan_message(db.message("Alive"), rule=None)
    assert (alive.state, alive.counters, alive.possible_fields) == (IntegrityState.NONE, (), ("AliveCounter",))
    assert alive.detail == POSSIBLE


POSSIBLE_NAMES = b"""BO_ 400 Mixed: 8 XXX
 SG_ COUNTER : 53|2@0+ (1,0) [0|3] "" XXX
 SG_ CHECKSUM : 63|8@0+ (1,0) [0|255] "" XXX
 SG_ LONG_COUNTER : 7|8@0+ (1,0) [0|255] "" XXX
 SG_ COUNTER_2 : 15|4@0+ (1,0) [0|15] "" XXX
BO_ 401 OnlyPossible: 8 XXX
 SG_ MSG_CNT_LOWER : 0|4@1+ (1,0) [0|15] "" XXX
BA_DEF_ SG_ "GenSigStartValue" INT 0 255;
BA_ "GenSigStartValue" SG_ 400 LONG_COUNTER 7;
"""


def test_possible_integrity_names_are_reported_not_generated(tmp_path: Path) -> None:
    db = _import(tmp_path, POSSIBLE_NAMES)
    plan = plan_message(db.message("Mixed"), rule="toyota")
    assert plan.state == IntegrityState.VALID
    assert [c.signal for c in plan.counters] == ["COUNTER"]
    assert [c.signal for c in plan.checksums] == ["CHECKSUM"]
    assert set(plan.possible_fields) == {"LONG_COUNTER", "COUNTER_2"}
    assert plan.detail == POSSIBLE
    payload = db.encode("Mixed", {"COUNTER_2": 9}, integrity_ok=True)
    long_counter, counter_2 = BitField(7, 8, False), BitField(15, 4, False)
    stamper = Stamper(plan)
    for _ in range(5):
        frame = stamper.stamp(payload)
        assert long_counter.read(frame) == long_counter.read(payload) == 7
        assert counter_2.read(frame) == 9

    only = plan_message(db.message("OnlyPossible"), rule="honda")
    assert (only.state, only.counters, only.checksums) == (IntegrityState.NONE, (), ())
    assert only.possible_fields == ("MSG_CNT_LOWER",)
    assert only.detail == POSSIBLE


SHORT = b"""BO_ 512 Short: 5 XXX
 SG_ SPEED : 7|16@0+ (1,0) [0|65535] "" XXX
 SG_ COUNTER : 31|4@0+ (1,0) [0|15] "" XXX
 SG_ CHECKSUM : 39|8@0+ (1,0) [0|255] "" XXX
"""


def test_short_dlc_message_checksum_covers_actual_length(tmp_path: Path) -> None:
    db = _import(tmp_path, SHORT)
    plan = plan_message(db.message("Short"), rule="toyota")
    assert plan.size == 5 and plan.state == IntegrityState.VALID
    seen: list[bytes] = []

    def recording(address: int, data: bytearray, field: BitField, signal: str) -> int:
        seen.append(bytes(data))
        return toyota_checksum(address, data, field, signal)

    plan = dataclasses.replace(plan, checksums=tuple(dataclasses.replace(c, fn=recording) for c in plan.checksums))
    stamper = Stamper(plan)
    payload = db.encode("Short", {"SPEED": 0x1234}, integrity_ok=True)
    first, second = stamper.stamp(payload), stamper.stamp(payload)
    assert [len(frame) for frame in seen] == [5, 5]
    # Toyota: length + the address bytes (0x02, 0x00) + every byte but the last.
    assert first == bytes([0x12, 0x34, 0x00, 0x00, (5 + 2 + 0x12 + 0x34) & 0xFF])
    assert second == bytes([0x12, 0x34, 0x00, 0x10, (5 + 2 + 0x12 + 0x34 + 0x10) & 0xFF])


NIBBLE = b"""BO_ 400 Nibble: 8 XXX
 SG_ DATA : 7|8@0+ (1,0) [0|255] "" XXX
 SG_ HIGH : 63|2@0+ (1,0) [0|3] "" XXX
 SG_ COUNTER : 61|2@0+ (1,0) [0|3] "" XXX
 SG_ CHECKSUM : 59|4@0+ (1,0) [0|15] "" XXX
"""


def test_nibble_aligned_checksum_does_not_disturb_neighbouring_bits(tmp_path: Path) -> None:
    plan = plan_message(_import(tmp_path, NIBBLE).message("Nibble"), rule="honda")
    assert plan.state == IntegrityState.VALID
    stamper = Stamper(plan)
    ones = b"\xff" * 8
    for k in range(5):
        frame = stamper.stamp(ones)
        assert frame[:7] == ones[:7]
        assert frame[7] >> 6 == 0b11
        assert (frame[7] >> 4) & 0b11 == k % 4
        cleared = bytearray(frame)
        BitField(59, 4, False).write(cleared, 0)
        assert frame[7] & 0xF == honda_checksum(400, cleared, BitField(59, 4, False), "CHECKSUM")


def test_checksum_field_is_zeroed_before_the_rule_reads_it() -> None:
    # Toyota sums every byte but the last, so a CHECKSUM in byte 0 is read by its own rule.
    plan = plan_message(_synthetic([("CHECKSUM", 7, 8, False)], 4), rule="toyota")
    field = plan.checksums[0].field
    expected = toyota_checksum(plan.address, bytearray(4), field, "CHECKSUM")
    for data in (bytes(4), b"\xff\x00\x00\x00", b"\x5a\x00\x00\x00"):
        assert Stamper(plan).stamp(data) == bytes([expected, 0, 0, 0]), data.hex()


def test_each_checksum_is_written_before_the_next_is_computed() -> None:
    first, second = BitField(0, 8, True), BitField(8, 8, True)
    seen: list[tuple[str, bytes]] = []

    def recording(address: int, data: bytearray, field: BitField, signal: str) -> int:
        seen.append((signal, bytes(data)))
        return 0xA5 if signal == "FIRST" else 0x3C

    plan = dataclasses.replace(
        plan_message(_synthetic([], 4), rule="toyota"),
        checksums=(ChecksumField("FIRST", first, recording), ChecksumField("SECOND", second, recording)),
    )
    frame = Stamper(plan).stamp(b"\x11\x22\x33\x44")
    assert seen == [("FIRST", b"\x00\x00\x33\x44"), ("SECOND", b"\xa5\x00\x33\x44")]
    assert frame == b"\xa5\x3c\x33\x44"


OVERLAP = b"""BO_ 1 Overlap: 2 XXX
 SG_ A : 0|8@1+ (1,0) [0|0] "" XXX
 SG_ COUNTER : 0|8@1+ (1,0) [0|0] "" XXX
 SG_ CHECKSUM : 8|8@1+ (1,0) [0|0] "" XXX
"""


def test_layout_error_message_is_unsupported(tmp_path: Path) -> None:
    message = _import(tmp_path, SIMPLE + OVERLAP).message("Overlap")
    assert message["layout_issue"]
    for rule in ("toyota", None):
        plan = plan_message(message, rule=rule)
        assert plan.state == IntegrityState.UNSUPPORTED
        assert plan.detail == message["layout_issue"]
        assert plan.counters == () and plan.checksums == () and plan.possible_fields == ()


def _synthetic(signals: list[tuple[str, int, int, bool]], dlc: int) -> dict[str, Any]:
    return {
        "name": "Synthetic",
        "id": 0x123,
        "extended": False,
        "dlc": dlc,
        "layout_issue": None,
        "integrity_fields": [name for name, *_ in signals],
        "signals": [{"name": n, "start": s, "length": size, "little_endian": le} for n, s, size, le in signals],
    }


def test_field_outside_the_frame_is_unsupported() -> None:
    cases = [
        ([("COUNTER", 20, 4, True), ("CHECKSUM", 8, 8, True)], "COUNTER"),
        ([("COUNTER", 0, 4, True), ("CHECKSUM", 12, 8, True)], "CHECKSUM"),
        ([("COUNTER", 0, 4, True), ("CHECKSUM", 15, 12, False)], "CHECKSUM"),
    ]
    for signals, culprit in cases:
        plan = plan_message(_synthetic(signals, 2), rule="toyota")
        assert plan.state == IntegrityState.UNSUPPORTED, signals
        assert plan.detail is not None and culprit in plan.detail
        assert plan.counters == () and plan.checksums == ()
    fits = plan_message(_synthetic([("COUNTER", 0, 4, True), ("CHECKSUM", 15, 8, False)], 2), rule="toyota")
    assert fits.state == IntegrityState.VALID


def test_stamper_rejects_wrong_length_and_does_not_mutate_input() -> None:
    plan = plan_message(_synthetic([("COUNTER", 0, 4, True), ("CHECKSUM", 56, 8, True)], 8), rule="toyota")
    stamper = Stamper(plan)
    for wrong in (bytes(7), bytes(9), b""):
        with pytest.raises(ValueError):
            stamper.stamp(wrong)
    data = bytearray(b"\x10\x20\x30\x40\x50\x60\x70\x80")
    frame = stamper.stamp(data)  # type: ignore[arg-type]
    assert data == bytearray(b"\x10\x20\x30\x40\x50\x60\x70\x80")
    assert isinstance(frame, bytes) and frame != bytes(data)


def test_counter_wraps_at_its_maximum() -> None:
    plan = plan_message(_synthetic([("COUNTER", 4, 2, True), ("CHECKSUM", 56, 8, True)], 8), rule="toyota")
    assert [c.max_value for c in plan.counters] == [3]
    stamper = Stamper(plan)
    field = plan.counters[0].field
    assert [field.read(stamper.stamp(bytes(8))) for _ in range(9)] == [0, 1, 2, 3, 0, 1, 2, 3, 0]


def test_counter_state_is_per_stamper() -> None:
    plan = plan_message(_synthetic([("COUNTER", 0, 4, True)], 8), rule=None)
    assert plan.state == IntegrityState.COUNTER_ONLY and plan.detail is None
    first, second = Stamper(plan), Stamper(plan)
    field = plan.counters[0].field
    assert [field.read(first.stamp(bytes(8))) for _ in range(3)] == [0, 1, 2]
    assert field.read(second.stamp(bytes(8))) == 0
    assert field.read(first.stamp(bytes(8))) == 3


def test_plan_types_are_frozen_values() -> None:
    plan = plan_message(_synthetic([("COUNTER", 0, 4, True), ("CHECKSUM", 56, 8, True)], 8), rule="toyota")
    assert isinstance(plan, MessagePlan)
    assert plan.counters == (CounterField("COUNTER", BitField(0, 4, True), 0, 15),)
    assert plan.checksums == (ChecksumField("CHECKSUM", BitField(56, 8, True), toyota_checksum),)
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.size = 3  # type: ignore[misc]
    assert {s.value for s in IntegrityState} == {"none", "valid", "counter_only", "user_rule", "unsupported"}


def _user_rule(message: dict[str, Any], *, counter: str | None, checksum: str | None, **spec: Any) -> UserRule:
    """A user rule over ``message``'s own signals: sum8 into ``checksum``, ``counter`` advancing from 0."""
    fields = {s["name"]: BitField(s["start"], s["length"], s["little_endian"]) for s in message["signals"]}
    counters = (CounterField(counter, fields[counter], 0, (1 << fields[counter].size) - 1),) if counter else ()
    checksums = ()
    if checksum:
        checksum_spec = ChecksumSpec.model_validate({"signal": checksum, "algorithm": "sum8", **spec})
        checksums = (ChecksumField(checksum, fields[checksum], build_checksum(checksum_spec, fields[checksum].size)),)
    return UserRule(message["name"], counters, checksums)


USER_DETAIL = "computed as the supplied rule says"


def test_user_rule_wins_over_the_named_rule() -> None:
    # Under toyota COUNTER and CHECKSUM are the fields; the user rule names SEQ and SUM instead.
    message = _synthetic([("COUNTER", 0, 4, True), ("SEQ", 4, 4, True), ("CHECKSUM", 56, 8, True), ("SUM", 48, 8, True)], 8)
    message["integrity_fields"] = ["COUNTER", "CHECKSUM"]
    named = plan_message(message, rule="toyota")
    assert [c.signal for c in named.counters] == ["COUNTER"] and named.state == IntegrityState.VALID

    user = _user_rule(message, counter="SEQ", checksum="SUM")
    plan = plan_message(message, rule="toyota", user=user)
    assert plan.state == IntegrityState.USER_RULE
    assert [c.signal for c in plan.counters] == ["SEQ"]
    assert [c.signal for c in plan.checksums] == ["SUM"]
    # The named rule's own signals stay what they were: reported as possible, never generated.
    assert set(plan.possible_fields) == {"COUNTER", "CHECKSUM"}
    assert plan.detail == f"{USER_DETAIL}; {POSSIBLE}"
    assert (plan.address, plan.extended, plan.size) == (0x123, False, 8)

    # With no named rule at all, the user rule still applies.
    bare = plan_message(message, rule=None, user=user)
    assert bare.state == IntegrityState.USER_RULE and bare.counters == plan.counters and bare.checksums == plan.checksums


def test_user_rule_detail_is_exact_when_it_covers_every_integrity_name() -> None:
    message = _synthetic([("COUNTER", 0, 4, True), ("CHECKSUM", 56, 8, True)], 8)
    plan = plan_message(message, rule="honda", user=_user_rule(message, counter="COUNTER", checksum="CHECKSUM"))
    assert (plan.state, plan.detail, plan.possible_fields) == (IntegrityState.USER_RULE, USER_DETAIL, ())


def test_stamping_with_a_user_rule_reproduces_the_algorithm_output() -> None:
    message = _synthetic([("DATA", 8, 8, True), ("SEQ", 0, 4, True), ("SUM", 56, 8, True)], 8)
    user = _user_rule(message, counter="SEQ", checksum="SUM", covers=[0, 1, 2, 3, 4, 5, 6], init=0x10, data_id=0x03)
    plan = plan_message(message, rule="toyota", user=user)
    stamper = Stamper(plan)
    seq, data, total = BitField(0, 4, True), BitField(8, 8, True), BitField(56, 8, True)
    payload = bytearray(8)
    data.write(payload, 0x2C)
    for k in range(20):
        frame = stamper.stamp(bytes(payload))
        assert seq.read(frame) == k % 16
        # sum8 with init 0x10 over bytes 0 to 6 (the counter nibble and 0x2C) and the data id 0x03, computed by hand.
        assert total.read(frame) == (0x10 + (k % 16) + 0x2C + 0x03) & 0xFF, k
        assert data.read(frame) == 0x2C


def test_user_rule_counter_wraps_at_its_own_maximum() -> None:
    message = _synthetic([("SEQ", 0, 4, True)], 2)
    fields = BitField(0, 4, True)
    user = UserRule("Synthetic", (CounterField("SEQ", fields, 0, 5),), ())
    stamper = Stamper(plan_message(message, rule=None, user=user))
    assert [fields.read(stamper.stamp(bytes(2))) for _ in range(8)] == [0, 1, 2, 3, 4, 5, 0, 1]


def test_user_rule_on_a_layout_error_is_unsupported() -> None:
    message = _synthetic([("COUNTER", 0, 4, True), ("CHECKSUM", 56, 8, True)], 8)
    user = _user_rule(message, counter="COUNTER", checksum="CHECKSUM")
    message["layout_issue"] = "Signal overlap"
    for rule in ("toyota", None):
        plan = plan_message(message, rule=rule, user=user)
        assert plan.state == IntegrityState.UNSUPPORTED
        assert plan.detail == "Signal overlap"
        assert plan.counters == () and plan.checksums == () and plan.possible_fields == ()


def test_user_rule_field_outside_the_frame_is_unsupported() -> None:
    message = _synthetic([("COUNTER", 0, 4, True), ("CHECKSUM", 56, 8, True)], 8)
    user = _user_rule(message, counter="COUNTER", checksum="CHECKSUM")
    short = {**message, "dlc": 4}  # the user rule was bound to the 8-byte layout; the message now has 4 bytes
    plan = plan_message(short, rule=None, user=user)
    assert plan.state == IntegrityState.UNSUPPORTED
    assert plan.detail is not None and "CHECKSUM" in plan.detail and "4-byte frame" in plan.detail
    assert plan.counters == () and plan.checksums == ()


def test_without_a_user_rule_the_plan_is_unchanged() -> None:
    message = _synthetic([("COUNTER", 0, 4, True), ("CHECKSUM", 56, 8, True)], 8)
    assert plan_message(message, rule="toyota", user=None) == plan_message(message, rule="toyota")
