"""Declarative rule files: what an uploaded YAML file may say, what it is refused for, and how it binds to messages.

A rule file is data only. The GUI server can be reached from a bench network, so a hostile or malformed file must be
refused with the dotted path of the problem and never partly applied: ``parse_rule_text`` checks the whole file, and
``RuleFile.resolve`` checks every rule against the DBC's messages, before anything is stamped.
"""

import dataclasses
import hashlib
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator.car.bits import BitField
from ecu_simulator.car.integrity import IntegrityState, Stamper, plan_message
from ecu_simulator.car.rules import (
    MAX_COUNTER_MAX,
    MAX_COVERS,
    MAX_COVERS_INDEX,
    MAX_DATA_ID,
    MAX_ECHOED_CHARS,
    MAX_RULE_BYTES,
    MAX_RULES,
    RuleError,
    RuleStore,
    UserRule,
    parse_rule_text,
)
from ecu_simulator.dbc import DbcStore

GEARBOX_DBC = b"""BO_ 801 Gearbox: 8 XXX
 SG_ DATA : 0|8@1+ (1,0) [0|255] "" XXX
 SG_ ALIVE : 48|4@1+ (1,0) [0|15] "" XXX
 SG_ CRC : 56|8@1+ (1,0) [0|255] "" XXX
"""

GEARBOX_RULES = """\
version: 1
rules:
  - messages: [Gearbox]
    counter:
      signal: ALIVE
      max: 9
    checksum:
      signal: CRC
      algorithm: sum8
      covers: [0, 1, 2, 3, 4, 5, 6]
      data_id: 0x5A
"""

# Eight bytes: DATA in byte 0, ALIVE in the low nibble of byte 6, CRC in byte 7.
DEFAULT_SIGNALS = [("DATA", 0, 8, True), ("ALIVE", 48, 4, True), ("CRC", 56, 8, True)]


def _message(
    name: str = "Gearbox",
    *,
    dlc: int = 8,
    signals: Sequence[tuple[str, int, int, bool]] = tuple(DEFAULT_SIGNALS),
    layout_issue: str | None = None,
) -> dict[str, Any]:
    """A message as ``ImportedDb.message`` returns it; one with a layout issue has no usable signal list."""
    listed = [] if layout_issue else signals
    return {
        "name": name,
        "id": 0x321,
        "extended": False,
        "dlc": dlc,
        "layout_issue": layout_issue,
        "integrity_fields": [],
        "signals": [
            {"name": n, "start": s, "length": size, "little_endian": le, "min": 0, "max": 255, "default": 0}
            for n, s, size, le in listed
        ],
    }


def _doc(*rules: str) -> str:
    """A rule file of the given rules; each rule is written as a block of lines at column 0."""
    body = "".join("  - " + rule.strip("\n").replace("\n", "\n    ") + "\n" for rule in rules)
    return "version: 1\nrules:\n" + body


SUM8 = "messages: [Gearbox]\nchecksum:\n  signal: CRC\n  algorithm: sum8\n"


def _checksum(**fields: Any) -> str:
    lines = "".join(f"  {key}: {value}\n" for key, value in fields.items())
    return "messages: [Gearbox]\nchecksum:\n  signal: CRC\n" + lines


def _padded(doc: str, size: int) -> str:
    """``doc`` followed by comment and blank lines to exactly ``size`` UTF-8 bytes."""
    pad = size - len(doc.encode("utf-8"))
    assert pad >= 0
    comment = "#" + "x" * 98 + "\n"
    count, rest = divmod(pad, len(comment))
    return doc + comment * count + "\n" * rest


def _import_dbc(tmp_path: Path, raw: bytes) -> Any:
    store = DbcStore(tmp_path)
    return store.load(store.import_file("synthetic.dbc", raw)["sha256"])


# --- A rule file that is valid -------------------------------------------------------------------------------------


def test_valid_rule_file_plans_user_rule_state(tmp_path: Path) -> None:
    message = _import_dbc(tmp_path, GEARBOX_DBC).message("Gearbox")
    resolved = parse_rule_text(GEARBOX_RULES).resolve([message])
    assert set(resolved) == {"Gearbox"}
    user = resolved["Gearbox"]
    assert isinstance(user, UserRule) and user.message == "Gearbox"

    plan = plan_message(message, rule=None, user=user)
    assert plan.state == IntegrityState.USER_RULE
    assert plan.detail == "computed as the supplied rule says"
    assert [(c.signal, c.max_value, c.shift) for c in plan.counters] == [("ALIVE", 9, 0)]
    assert [c.signal for c in plan.checksums] == ["CRC"]

    stamper = Stamper(plan)
    payload = bytearray(8)
    payload[0] = 0x21
    alive, crc = BitField(48, 4, True), BitField(56, 8, True)
    for k in range(12):
        frame = stamper.stamp(bytes(payload))
        assert alive.read(frame) == k % 10  # the counter wraps after its max of 9
        # sum8 over bytes 0 to 6 and the data id 0x5A: 0x21 + the counter + 0x5A.
        assert crc.read(frame) == (0x21 + k % 10 + 0x5A) & 0xFF, k


def test_counter_only_and_checksum_only_rules_are_valid() -> None:
    counter_only = parse_rule_text(_doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n"))
    user = counter_only.resolve([_message()])["Gearbox"]
    assert [c.signal for c in user.counters] == ["ALIVE"] and user.checksums == ()
    assert user.counters[0].max_value == 15  # no max given: the field's own maximum
    checksum_only = parse_rule_text(_doc(SUM8)).resolve([_message()])["Gearbox"]
    assert checksum_only.counters == () and [c.signal for c in checksum_only.checksums] == ["CRC"]


def test_hex_numbers_are_accepted() -> None:
    rule_file = parse_rule_text(_doc(_checksum(algorithm="crc8", poly="0x1D", init="0xFF", xorout="0xFF")))
    checksum = rule_file.rules[0].checksum
    assert checksum is not None and (checksum.poly, checksum.init, checksum.xorout) == (0x1D, 0xFF, 0xFF)


def test_the_field_sizes_follow_the_signals_of_the_message() -> None:
    # A 4-bit Motorola checksum: the function built for it keeps the low nibble of the sum.
    message = _message(signals=[("DATA", 0, 8, True), ("CRC", 15, 4, False)], dlc=4)
    rules = parse_rule_text(_doc("messages: [Gearbox]\nchecksum:\n  signal: CRC\n  algorithm: sum8\n  covers: [0]\n"))
    checksum = rules.resolve([message])["Gearbox"].checksums[0]
    assert checksum.field == BitField(15, 4, False)
    assert checksum.fn(0x321, bytearray(b"\xf5\x00\x00\x00"), checksum.field, "CRC") == 0x5


def test_models_are_frozen() -> None:
    rule_file = parse_rule_text(GEARBOX_RULES)
    with pytest.raises(ValueError, match="frozen"):
        rule_file.version = 2  # type: ignore[assignment,misc]
    user = rule_file.resolve([_message()])["Gearbox"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        user.message = "other"  # type: ignore[misc]


# --- A rule file that is refused: parse time ------------------------------------------------------------------------

# Ten levels of nine aliases each: expanded, a billion strings. Refused from the parser's events, never expanded.
BILLION_LAUGHS = 'a: &a ["lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol"]\n' + "".join(
    f"{nxt}: &{nxt} [{', '.join(['*' + prev] * 9)}]\n" for prev, nxt in zip("abcdefghi", "bcdefghij", strict=True)
)
BIG_INT = "9" * 5000
DECIMAL_4000 = "9" * 4000  # under the interpreter's 4300-digit conversion limit, so the YAML loader reads it
HEX_5000 = "0x" + "f" * 5000  # about 6000 decimal digits: read as an int, but not printable with str()
TAGS_REFUSED = "tags are not allowed in a rule file"
LONG = "k" * 5000


def _tagged(value: str) -> str:
    """A rule file whose counter ``max`` is written as ``value``, normally a tagged scalar."""
    return _doc(f"messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: {value}\n")


def _huge(field: str, value: str, algorithm: str = "crc8") -> str:
    """A checksum rule whose ``field`` is ``value``; the other fields are valid for ``algorithm``."""
    fields: dict[str, Any] = {"poly": 1} if algorithm.startswith("crc") else {}
    return _doc(_checksum(algorithm=algorithm, **(fields | {field: value})))


DEEP = "a: " + "[" * 500 + "]" * 500 + "\n"

REJECTS = [
    # path, text, wording that must appear in the message
    pytest.param(
        _doc(_checksum(algorithm="md5")), "rules.0.checksum.algorithm", "'crc16'", id="unknown-algorithm"
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc8", poly="0x1FF")),
        "rules.0.checksum.poly",
        "value 511 does not fit an 8-bit CRC",
        id="crc8-poly-too-wide",
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc16", poly="0x10000")),
        "rules.0.checksum.poly",
        "value 65536 does not fit a 16-bit CRC",
        id="crc16-poly-too-wide",
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc8", poly=7, init="0x100")),
        "rules.0.checksum.init",
        "value 256 does not fit an 8-bit CRC",
        id="crc8-init-too-wide",
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc16", poly="0x1021", xorout="0x10000")),
        "rules.0.checksum.xorout",
        "does not fit a 16-bit CRC",
        id="crc16-xorout-too-wide",
    ),
    pytest.param(
        _doc(_checksum(algorithm="xor", init="0x100")),
        "rules.0.checksum.init",
        "does not fit an 8-bit xor",
        id="xor-init-too-wide",
    ),
    pytest.param(
        _doc(_checksum(algorithm="nibble_sum", xorout="0x10")),
        "rules.0.checksum.xorout",
        "does not fit a 4-bit nibble_sum",
        id="nibble-sum-xorout-too-wide",
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc8", poly=-1)), "rules.0.checksum.poly", "does not fit", id="negative-poly"
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", init=-1)), "rules.0.checksum.init", "does not fit", id="negative-init"
    ),
    pytest.param(_doc(_checksum(algorithm="crc8")), "rules.0.checksum.poly", "needs a poly", id="crc-without-poly"),
    pytest.param(_doc(_checksum(algorithm="crc16", poly=0)), "rules.0.checksum.poly", "not a CRC", id="zero-poly"),
    pytest.param(
        _doc(_checksum(algorithm="xor", poly=7)), "rules.0.checksum.poly", "applies only to crc8 and crc16", id="poly-on-xor"
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", reflect="true")),
        "rules.0.checksum.reflect",
        "applies only to crc8 and crc16",
        id="reflect-on-sum8",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", covers="[0, -1]")),
        "rules.0.checksum.covers.1",
        "greater than or equal to 0",
        id="negative-covers-index",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", covers=f"[{', '.join(['0'] * 65)}]")),
        "rules.0.checksum.covers",
        "at most 64",
        id="too-many-covers",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", data_id=256)),
        "rules.0.checksum.data_id",
        "less than or equal to 255",
        id="data-id-too-big",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", data_id=-1)),
        "rules.0.checksum.data_id",
        "greater than or equal to 0",
        id="data-id-negative",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\nglob: 'G*'\n" + "counter:\n  signal: ALIVE\n"),
        "rules.0",
        "exactly one of messages and glob",
        id="messages-and-glob",
    ),
    pytest.param(
        _doc("counter:\n  signal: ALIVE\n"), "rules.0", "exactly one of messages and glob", id="neither-messages-nor-glob"
    ),
    pytest.param(
        _doc("messages: [Gearbox]\n"), "rules.0", "a counter, a checksum or both", id="rule-with-nothing-to-do"
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\nchecksum:\n  signal: ALIVE\n  algorithm: xor\n"),
        "rules.0",
        "same signal",
        id="counter-and-checksum-share-a-signal",
    ),
    pytest.param(
        _doc("glob: '" + "G" * 65 + "'\ncounter:\n  signal: ALIVE\n"),
        "rules.0.glob",
        "at most 64 characters",
        id="glob-over-64-characters",
    ),
    pytest.param(
        _doc("glob: ''\ncounter:\n  signal: ALIVE\n"), "rules.0.glob", "at least 1 character", id="empty-glob"
    ),
    pytest.param(
        _doc("messages: []\ncounter:\n  signal: ALIVE\n"), "rules.0.messages", "at least 1 item", id="empty-messages"
    ),
    pytest.param(
        _doc("messages: [3]\ncounter:\n  signal: ALIVE\n"), "rules.0.messages.0", "valid string", id="message-not-a-string"
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ''\n"), "rules.0.counter.signal", "at least 1", id="empty-signal"
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: -1\n"),
        "rules.0.counter.max",
        "greater than or equal to 0",
        id="negative-counter-max",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\nbogus: 1\n"),
        "rules.0.bogus",
        "Extra inputs are not permitted",
        id="unknown-rule-key",
    ),
    pytest.param(
        _doc(_checksum(algorithm="xor", bogus=1)),
        "rules.0.checksum.bogus",
        "Extra inputs are not permitted",
        id="unknown-checksum-key",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  bogus: 1\n"),
        "rules.0.counter.bogus",
        "Extra inputs are not permitted",
        id="unknown-counter-key",
    ),
    pytest.param(
        GEARBOX_RULES + "extra: 1\n", "extra", "Extra inputs are not permitted", id="unknown-top-level-key"
    ),
    pytest.param(
        _doc(_checksum(algorithm="xor")).replace("version: 1", "version: 2"),
        "version",
        "version 2",
        id="version-2",
    ),
    pytest.param(GEARBOX_RULES.replace("version: 1\n", ""), "version", "Field required", id="version-missing"),
    pytest.param(GEARBOX_RULES.replace("version: 1", "version: true"), "version", "integer 1", id="version-true"),
    pytest.param(GEARBOX_RULES.replace("version: 1", "version: 1.0"), "version", "integer 1", id="version-float"),
    pytest.param(GEARBOX_RULES.replace("version: 1", "version: '1'"), "version", "integer 1", id="version-string"),
    pytest.param("version: 1\n", "rules", "Field required", id="rules-missing"),
    pytest.param("version: 1\nrules: 5\n", "rules", "valid list", id="rules-not-a-list"),
    pytest.param("version: 1\nrules:\n  - 5\n", "rules.0", "valid dictionary", id="rule-not-a-mapping"),
    pytest.param(
        _doc(_checksum(algorithm="crc8", poly="true")), "rules.0.checksum.poly", "valid integer", id="bool-for-an-int"
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc8", poly="7.0")), "rules.0.checksum.poly", "valid integer", id="float-for-an-int"
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc8", poly="'7'")), "rules.0.checksum.poly", "valid integer", id="string-for-an-int"
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", init="true")), "rules.0.checksum.init", "valid integer", id="bool-for-init"
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", covers="[0, true]")),
        "rules.0.checksum.covers.1",
        "valid integer",
        id="bool-in-covers",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", data_id="1.0")),
        "rules.0.checksum.data_id",
        "valid integer",
        id="float-for-data-id",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", reflect="1")), "rules.0.checksum.reflect", "valid boolean", id="int-for-reflect"
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: true\n"),
        "rules.0.counter.max",
        "valid integer",
        id="bool-for-counter-max",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: 5\n"), "rules.0.counter.signal", "valid string", id="int-for-signal"
    ),
    pytest.param(
        _doc(SUM8) + "  - messages: [A]\n    checksum:\n      signal: S\n      algorithm: crc8\n",
        "rules.1.checksum.poly",
        "needs a poly",
        id="path-names-the-rule-index",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n    max: 1\n"), "", "not valid YAML", id="yaml-syntax-error"
    ),
    pytest.param("", "", "empty", id="empty-text"),
    pytest.param("\n# only a comment\n", "", "empty", id="only-a-comment"),
    pytest.param("- 1\n- 2\n", "", "must be a mapping at the top level", id="top-level-list"),
    pytest.param("just a string\n", "", "must be a mapping at the top level", id="top-level-scalar"),
    pytest.param(
        "version: 1\nrules: []\nrules: []\n", "", "duplicate key", id="duplicate-key"
    ),
    pytest.param(
        GEARBOX_RULES.replace("algorithm: sum8", "algorithm: sum8\n      algorithm: xor"),
        "",
        "duplicate key",
        id="duplicate-key-nested",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: &names [Gearbox]\n    counter:\n      signal: ALIVE\n",
        "",
        "anchor",
        id="anchor",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: &names [Gearbox]\n    counter:\n      signal: ALIVE\n"
        "  - messages: *names\n    counter:\n      signal: ALIVE\n",
        "",
        "anchor",
        id="anchor-and-alias",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: [Gearbox]\n    counter: *missing\n", "", "alias", id="alias-without-anchor"
    ),
    pytest.param(BILLION_LAUGHS, "", "anchor at line 1", id="billion-laughs"),
    pytest.param(
        "version: 1\nbase: &c {signal: ALIVE}\nrules:\n  - messages: [Gearbox]\n    counter:\n      <<: *c\n",
        "",
        "anchor",
        id="merge-key-through-an-anchor",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: [Gearbox]\n    counter:\n      <<: {signal: ALIVE}\n",
        "",
        "merge key",
        id="merge-key-with-an-inline-mapping",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: [Gearbox]\n    counter:\n      <<: [{signal: ALIVE}]\n",
        "",
        "merge key",
        id="merge-key-with-a-list",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: !!python/object/apply:os.system ['true']\n",
        "",
        TAGS_REFUSED,
        id="python-object-tag",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: [!!python/name:os.system ]\n", "", TAGS_REFUSED, id="python-name-tag"
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: !!binary Z2Vhcm5ib3g=\n    counter: {signal: A}\n",
        "",
        TAGS_REFUSED,
        id="binary-tag",
    ),
    pytest.param(
        GEARBOX_RULES + "---\nversion: 1\nrules: []\n", "", "not valid YAML", id="second-document-in-the-stream"
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: !!set {Gearbox}\n    counter: {signal: A}\n",
        "",
        TAGS_REFUSED,
        id="set-tag",
    ),
    # Any explicit tag is refused before anything is constructed: the safe constructor raises KeyError for
    # `!!bool foo` and IndexError for `!!int ''`, neither a YAMLError nor a ValueError.
    pytest.param(_tagged("!!bool foo"), "", TAGS_REFUSED, id="tag-bool-foo"),
    pytest.param(_tagged("!!int ''"), "", TAGS_REFUSED, id="tag-int-empty"),
    pytest.param(_tagged("!!float ''"), "", TAGS_REFUSED, id="tag-float-empty"),
    pytest.param(_tagged("!!timestamp 2001-01-01"), "", TAGS_REFUSED, id="tag-timestamp"),
    pytest.param(_tagged("!!binary Z2Vhcm5ib3g="), "", TAGS_REFUSED, id="tag-binary-on-a-number-field"),
    pytest.param(_tagged("!!set {a}"), "", TAGS_REFUSED, id="tag-set-on-a-number-field"),
    pytest.param(_tagged("!!str x"), "", TAGS_REFUSED, id="tag-str"),
    pytest.param(_tagged("!!map {a: 1}"), "", TAGS_REFUSED, id="tag-map"),
    pytest.param(_tagged("!!seq [1]"), "", TAGS_REFUSED, id="tag-seq"),
    pytest.param(_tagged("! 3"), "", TAGS_REFUSED, id="non-specific-tag"),
    pytest.param(_tagged("!<tag:yaml.org,2002:int> 3"), "", TAGS_REFUSED, id="verbatim-tag"),
    pytest.param(
        GEARBOX_RULES.replace("signal: ALIVE", "signal: !!str ALIVE"), "", TAGS_REFUSED, id="tag-on-an-otherwise-valid-value"
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter: !!map\n  signal: ALIVE\n"),
        "",
        TAGS_REFUSED,
        id="tag-on-a-mapping",
    ),
    # Integers are bounded at parse time, and no message shows a number wider than 64 bits.
    pytest.param(
        _huge("poly", DECIMAL_4000), "rules.0.checksum.poly", "does not fit an 8-bit CRC", id="poly-4000-digit-decimal"
    ),
    pytest.param(
        _huge("poly", HEX_5000), "rules.0.checksum.poly", "does not fit an 8-bit CRC", id="poly-5000-digit-hex"
    ),
    pytest.param(
        _huge("poly", "-" + DECIMAL_4000), "rules.0.checksum.poly", "does not fit", id="poly-negative-4000-digit"
    ),
    pytest.param(
        _huge("init", HEX_5000), "rules.0.checksum.init", "does not fit an 8-bit CRC", id="init-5000-digit-hex"
    ),
    pytest.param(
        _huge("xorout", DECIMAL_4000, "crc16"), "rules.0.checksum.xorout", "does not fit a 16-bit CRC", id="xorout-4000-digit"
    ),
    pytest.param(
        _huge("init", HEX_5000, "nibble_sum"), "rules.0.checksum.init", "does not fit a 4-bit nibble_sum", id="nibble-init-hex"
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", data_id=HEX_5000)),
        "rules.0.checksum.data_id",
        "less than or equal to 255",
        id="data-id-5000-digit-hex",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", covers=f"[{HEX_5000}]")),
        "rules.0.checksum.covers.0",
        "less than or equal to 63",
        id="covers-item-5000-digit-hex",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", covers=f"[0, {DECIMAL_4000}]")),
        "rules.0.checksum.covers.1",
        "less than or equal to 63",
        id="covers-item-4000-digit-decimal",
    ),
    pytest.param(
        _doc(_checksum(algorithm="sum8", covers="[64]")),
        "rules.0.checksum.covers.0",
        "less than or equal to 63",
        id="covers-item-first-past-the-bound",
    ),
    pytest.param(
        _tagged(HEX_5000),
        "rules.0.counter.max",
        "less than or equal to 18446744073709551615",
        id="counter-max-5000-digit-hex",
    ),
    pytest.param(
        _tagged(DECIMAL_4000), "rules.0.counter.max", "less than or equal to 18446744073709551615", id="counter-max-4000-digit"
    ),
    pytest.param(
        _tagged(str(2**64)),
        "rules.0.counter.max",
        "less than or equal to 18446744073709551615",
        id="counter-max-first-past-the-bound",
    ),
    pytest.param(
        "version: " + DECIMAL_4000 + "\nrules: []\n", "version", "unsupported version", id="version-4000-digit-decimal"
    ),
    pytest.param("version: " + HEX_5000 + "\nrules: []\n", "version", "unsupported version", id="version-5000-digit-hex"),
    pytest.param(
        "version: 1\nrules:\n  - messages: [Gearbox]\n    counter: {signal: ALIVE}\n    ? " + LONG + "\n    : 1\n",
        "rules.0." + "k" * MAX_ECHOED_CHARS + "...",
        "Extra inputs are not permitted",
        id="very-long-unknown-key-is-clipped",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: " + LONG + "\nchecksum:\n  signal: " + LONG + "\n  algorithm: xor\n"),
        "rules.0",
        "the same signal",
        id="very-long-shared-signal-is-clipped",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: [Gearbox]\n    checksum:\n      signal: CRC\n      algorithm: crc8\n"
        "      poly: " + "9" * 5000 + "\n",
        "",
        "an integer in it is too large",
        id="decimal-integer-over-the-loader-limit",
    ),
    pytest.param("version: 1\n" + DEEP, "", "nested more than", id="deeply-nested-flow"),
    pytest.param(
        "version: 1\nrules:\n" + "".join(f"{' ' * (2 * i)}- " for i in range(40)) + "x\n",
        "",
        "nested more than",
        id="deeply-nested-block",
    ),
    pytest.param(
        "version: 1\nrules:\n  - messages: [Gearbox]\n    checksum:\n      signal: CRC\n      algorithm: crc8\n"
        f"      poly: {BIG_INT}\n",
        "",
        "cannot be read",
        id="huge-integer",
    ),
    pytest.param(
        _doc(_checksum(algorithm="crc8", poly="2001-13-45")), "", "cannot be read", id="impossible-date-as-a-number"
    ),
    pytest.param("version: 1\nrules: []\n# \udc80\n", "", "UTF-8", id="text-that-is-not-utf-8"),
    pytest.param(
        _doc(_checksum(algorithm="sum8", **{"1": "x"})),
        "rules.0.checksum.1",
        "Keys should be strings",
        id="non-string-key",
    ),
    pytest.param("version: 1\nrules:\n" + "  - glob: '*'\n    counter: {signal: A}\n" * (MAX_RULES + 1), "rules",
                 "1001 rules", id="1001-rules"),
    pytest.param("# " + "x" * (MAX_RULE_BYTES - 1) + "\nversion: 1\nrules: []\n", "", "262144", id="over-the-size-limit"),
    pytest.param("version: 1\nrules: []\n# " + "x" * (300 * 1024) + "\n", "", "262144", id="300-KiB-file"),
    pytest.param("version: 1\nrules: []\n# " + "é" * 140_000 + "\n", "", "262144", id="limit-counts-utf8-bytes"),
]


@pytest.mark.parametrize(("text", "path", "needle"), REJECTS)
def test_rejects(text: str, path: str, needle: str) -> None:
    with pytest.raises(RuleError) as caught:
        parse_rule_text(text)
    error = caught.value
    message = str(error)
    assert error.path == path
    if path:
        assert message.startswith(f"{path}: "), message
    else:
        assert message.startswith("rule file"), message
    assert needle in message, message
    assert "profile" not in message, message
    assert isinstance(error, ValueError)
    # Whatever the file says, the refusal is short and carries no interpreter internals.
    assert len(message) <= 300, len(message)
    assert "set_int_max_str_digits" not in message and "Exceeds the limit" not in message, message


def test_a_rule_file_of_exactly_256_kib_is_accepted_and_one_byte_more_is_refused() -> None:
    assert MAX_RULE_BYTES == 256 * 1024
    exact = _padded(GEARBOX_RULES, MAX_RULE_BYTES)
    assert len(exact.encode("utf-8")) == MAX_RULE_BYTES
    assert parse_rule_text(exact) == parse_rule_text(GEARBOX_RULES)

    over = _padded(GEARBOX_RULES, MAX_RULE_BYTES + 1)
    with pytest.raises(RuleError, match="262145 bytes"):
        parse_rule_text(over)


def test_the_size_limit_is_measured_in_utf8_bytes_not_characters() -> None:
    # 131072 two-byte characters are 256 KiB of bytes on their own: the comment pushes the file past the limit
    # though its character count is well under it.
    text = GEARBOX_RULES + "# " + "é" * 131_072 + "\n"
    assert len(text) < MAX_RULE_BYTES < len(text.encode("utf-8"))
    with pytest.raises(RuleError, match="bytes"):
        parse_rule_text(text)
    fits = GEARBOX_RULES + "# " + "é" * 100_000 + "\n"
    assert parse_rule_text(fits).rules[0].messages == ["Gearbox"]


def test_exactly_1000_rules_are_accepted_and_1001_are_refused() -> None:
    assert MAX_RULES == 1000
    rule = "  - glob: 'G*'\n    counter:\n      signal: ALIVE\n"
    assert len(parse_rule_text("version: 1\nrules:\n" + rule * 1000).rules) == 1000
    with pytest.raises(RuleError, match="1001 rules") as caught:
        parse_rule_text("version: 1\nrules:\n" + rule * 1001)
    assert caught.value.path == "rules"


def test_the_rule_count_is_checked_before_the_rules_are_validated() -> None:
    # A thousand and one malformed rules are refused for their number, not by validating each one.
    with pytest.raises(RuleError) as caught:
        parse_rule_text("version: 1\nrules:\n" + "  - 5\n" * 1001)
    assert caught.value.path == "rules" and "1001 rules" in str(caught.value)


def test_a_rule_file_never_executes_anything(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    payloads = [
        f"!!python/object/apply:os.system ['touch {marker}']",
        f"!!python/object/apply:subprocess.check_output [['touch', '{marker}']]",
        f"!!python/object/new:os.system [touch {marker}]",
        "!!python/name:os.system",
    ]
    for payload in payloads:
        with pytest.raises(RuleError, match="rule file"):
            parse_rule_text(f"version: 1\nrules:\n  - messages: [{payload}]\n")
        with pytest.raises(RuleError, match="rule file"):
            parse_rule_text(f"version: 1\nrules: {payload}\n")
        with pytest.raises(RuleError, match="rule file"):
            parse_rule_text(f"{payload}\n")
    assert not marker.exists()


def test_a_quoted_merge_key_is_just_a_name() -> None:
    # Only a bare << is YAML's merge key; a quoted one is an ordinary (and here unknown) key.
    with pytest.raises(RuleError) as caught:
        parse_rule_text(_doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  '<<': 1\n"))
    assert caught.value.path == "rules.0.counter.<<"
    assert "Extra inputs are not permitted" in str(caught.value)


def test_a_message_name_that_looks_like_an_anchor_is_data() -> None:
    # '&' and '*' inside quotes or in the middle of a plain scalar are text, not anchors or aliases.
    parsed = parse_rule_text(_doc("messages: ['*all', 'a&b', 'x *y', '&z']\ncounter:\n  signal: ALIVE\n"))
    assert parsed.rules[0].messages == ["*all", "a&b", "x *y", "&z"]


# --- Resolving a rule file against the messages of a DBC ---------------------------------------------------------


def test_resolve_names_the_rule_for_each_message() -> None:
    text = _doc(
        "messages: [Gearbox]\ncounter:\n  signal: ALIVE\n",
        "glob: 'Eng*'\nchecksum:\n  signal: CRC\n  algorithm: xor\n",
    )
    messages = [_message("Gearbox"), _message("Engine"), _message("EngineExtra"), _message("Brakes")]
    resolved = parse_rule_text(text).resolve(messages)
    assert dict(resolved).keys() == {"Gearbox", "Engine", "EngineExtra"}
    assert [c.signal for c in resolved["Gearbox"].counters] == ["ALIVE"]
    assert [c.signal for c in resolved["Engine"].checksums] == ["CRC"]
    assert resolved["EngineExtra"].message == "EngineExtra"
    with pytest.raises(TypeError):
        resolved["Brakes"] = resolved["Gearbox"]  # type: ignore[index]


def test_the_first_rule_that_matches_wins() -> None:
    text = _doc(
        "glob: 'Gear*'\ncounter:\n  signal: ALIVE\n",
        "messages: [Gearbox]\nchecksum:\n  signal: CRC\n  algorithm: xor\n",
        "glob: '*'\nchecksum:\n  signal: CRC\n  algorithm: sum8\n",
    )
    rule_file = parse_rule_text(text)
    gearbox = rule_file.rule_for(_message("Gearbox"))
    assert gearbox is not None and gearbox.checksums == () and [c.signal for c in gearbox.counters] == ["ALIVE"]
    other = rule_file.rule_for(_message("Other"))
    assert other is not None and other.counters == () and len(other.checksums) == 1
    # The second rule matches only Gearbox, which the first already took, so it is shadowed and never applied. It still
    # matches a message, so it is not reported as matching nothing.
    assert set(rule_file.resolve([_message("Gearbox"), _message("Other")])) == {"Gearbox", "Other"}


def test_rule_for_is_none_when_no_rule_matches() -> None:
    rule_file = parse_rule_text(GEARBOX_RULES)
    assert rule_file.rule_for(_message("Other")) is None


def test_a_message_with_a_layout_issue_is_never_matched() -> None:
    rule_file = parse_rule_text(_doc("glob: '*'\ncounter:\n  signal: ALIVE\n"))
    broken = _message("Broken", layout_issue="Signal overlap")
    assert rule_file.rule_for(broken) is None
    resolved = rule_file.resolve([broken, _message("Good")])
    assert set(resolved) == {"Good"}


def test_a_rule_whose_only_matches_have_layout_issues_still_matches_something() -> None:
    rule_file = parse_rule_text(
        _doc(
            "messages: [Broken]\ncounter:\n  signal: ALIVE\n",
            "glob: 'Bro*'\ncounter:\n  signal: ALIVE\n",
            "glob: 'Good'\ncounter:\n  signal: ALIVE\n",
        )
    )
    resolved = rule_file.resolve([_message("Broken", layout_issue="Signal overlap"), _message("Good")])
    assert set(resolved) == {"Good"}


def test_glob_matching_is_case_sensitive() -> None:
    rule_file = parse_rule_text(_doc("glob: 'gear*'\ncounter:\n  signal: ALIVE\n"))
    assert rule_file.rule_for(_message("Gearbox")) is None
    assert rule_file.rule_for(_message("gearbox")) is not None
    by_name = parse_rule_text(_doc("messages: [gearbox]\ncounter:\n  signal: ALIVE\n"))
    assert by_name.rule_for(_message("Gearbox")) is None


@pytest.mark.parametrize(
    ("glob", "name", "matches"),
    [
        ("Gear.*", "GearX1", False),  # '.' is a dot, '*' a wildcard: "Gear." then anything
        ("Gear.*", "Gear.1", True),
        (".*", "Gearbox", False),
        ("^Gearbox$", "Gearbox", False),
        ("^Gearbox$", "^Gearbox$", True),
        ("Gear(box|case)", "Gearbox", False),
        ("Gear(box|case)", "Gear(box|case)", True),
        ("Gear+", "Gearr", False),
        ("Gear+", "Gear+", True),
        ("Gear?ox", "Gearbox", True),  # fnmatch's own '?' is one character
        ("Gear[a-z]ox", "Gearbox", True),  # and so is its [seq]
        ("Gear\\d", "Gear1", False),
        ("Gear{1,3}", "Gearrr", False),
    ],
)
def test_regular_expression_looking_globs_are_treated_literally(glob: str, name: str, matches: bool) -> None:
    quoted = "'" + glob.replace("'", "''") + "'"
    rule_file = parse_rule_text(_doc(f"glob: {quoted}\ncounter:\n  signal: ALIVE\n"))
    assert (rule_file.rule_for(_message(name)) is not None) is matches


@pytest.mark.parametrize("glob", ["[", "[!", "[]", "[!]", "[a-", "[[]", "\\", "?*" * 32, "*a" * 31 + "*b"])
def test_odd_globs_neither_raise_nor_stall(glob: str) -> None:
    quoted = "'" + glob.replace("'", "''") + "'"
    rule_file = parse_rule_text(_doc(f"glob: {quoted}\ncounter:\n  signal: ALIVE\n"))
    assert rule_file.rule_for(_message("a" * 1000)) is None or glob.startswith("?*")


RESOLVE_REJECTS = [
    # rules, the messages of the DBC, the dotted path of the problem, wording
    pytest.param(
        GEARBOX_RULES,
        [_message("Engine")],
        "rules.0.messages",
        "matches no message",
        id="messages-matching-nothing",
    ),
    pytest.param(
        _doc(SUM8, "glob: 'Zz*'\ncounter:\n  signal: ALIVE\n"),
        [_message("Gearbox")],
        "rules.1.glob",
        "matches no message",
        id="glob-matching-nothing",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ROLLING\n"),
        [_message()],
        "rules.0.counter.signal",
        "no signal named 'ROLLING'",
        id="unknown-counter-signal",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\nchecksum:\n  signal: SUM\n  algorithm: xor\n"),
        [_message()],
        "rules.0.checksum.signal",
        "no signal named 'SUM'",
        id="unknown-checksum-signal",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\nchecksum:\n  signal: " + LONG + "\n  algorithm: xor\n"),
        [_message()],
        "rules.0.checksum.signal",
        "no signal named '" + "k" * MAX_ECHOED_CHARS + "...'",
        id="unknown-long-signal-is-clipped",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: 16\n"),
        [_message()],
        "rules.0.counter.max",
        "value 16 does not fit the 4-bit signal ALIVE",
        id="counter-max-too-big-for-the-field",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\nchecksum:\n  signal: CRC\n  algorithm: sum8\n  covers: [0, 8]\n"),
        [_message()],
        "rules.0.checksum.covers.1",
        "byte 8 is past the 8-byte message",
        id="covers-index-past-the-message",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\nchecksum:\n  signal: CRC\n  algorithm: sum8\n  covers: [0, 4]\n"),
        [_message(dlc=4, signals=[("DATA", 0, 8, True), ("CRC", 24, 8, True)])],
        "rules.0.checksum.covers.1",
        "byte 4 is past the 4-byte message",
        id="covers-index-past-a-short-message",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\nchecksum:\n  signal: CRC\n  algorithm: crc16\n  poly: 0x1021\n"),
        [_message(dlc=2, signals=[("DATA", 0, 8, True), ("CRC", 8, 16, True)])],
        "rules.0.checksum.signal",
        "reaches outside the 2-byte frame",
        id="field-wider-than-the-message",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\nchecksum:\n  signal: CRC\n  algorithm: xor\n"),
        [_message(dlc=2, signals=[("DATA", 0, 8, True), ("CRC", 12, 8, True)])],
        "rules.0.checksum.signal",
        "reaches outside the 2-byte frame",
        id="checksum-field-outside-the-frame",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n"),
        [_message(dlc=2, signals=[("DATA", 0, 8, True), ("ALIVE", 20, 4, True)])],
        "rules.0.counter.signal",
        "reaches outside the 2-byte frame",
        id="counter-field-outside-the-frame",
    ),
    pytest.param(
        _doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n"),
        [_message("Other"), _message("Gearbox", signals=[("DATA", 0, 8, True)])],
        "rules.0.counter.signal",
        "message 'Gearbox' has no signal named 'ALIVE'",
        id="a-later-message-is-checked-too",
    ),
]


@pytest.mark.parametrize(("text", "messages", "path", "needle"), RESOLVE_REJECTS)
def test_resolve_rejects(text: str, messages: list[dict[str, Any]], path: str, needle: str) -> None:
    rule_file = parse_rule_text(text)
    with pytest.raises(RuleError) as caught:
        rule_file.resolve(messages)
    message = str(caught.value)
    assert caught.value.path == path
    assert message.startswith(f"{path}: "), message
    assert needle in message, message
    assert "profile" not in message
    assert len(message) <= 300, len(message)


def test_rule_for_rejects_an_invalid_match_with_the_same_path() -> None:
    rule_file = parse_rule_text(_doc("glob: 'G*'\nchecksum:\n  signal: SUM\n  algorithm: xor\n"))
    with pytest.raises(RuleError) as caught:
        rule_file.rule_for(_message("Gearbox"))
    assert caught.value.path == "rules.0.checksum.signal"
    assert rule_file.rule_for(_message("Brakes")) is None


def test_a_shadowed_rule_is_not_checked_against_the_message_that_shadows_it() -> None:
    # Rule 1 names a signal Gearbox lacks, but rule 0 takes Gearbox first, so rule 1 never applies to it.
    rule_file = parse_rule_text(
        _doc(
            "messages: [Gearbox]\ncounter:\n  signal: ALIVE\n",
            "messages: [Gearbox, Brakes]\ncounter:\n  signal: ROLLING\n",
        )
    )
    resolved = rule_file.resolve([_message("Gearbox"), _message("Brakes", signals=[("ROLLING", 0, 4, True)])])
    assert [c.signal for c in resolved["Gearbox"].counters] == ["ALIVE"]
    assert [c.signal for c in resolved["Brakes"].counters] == ["ROLLING"]


def test_resolve_does_not_partly_apply_an_invalid_file() -> None:
    # The second message's rule is invalid: nothing is returned, not even the first message's good rule.
    rule_file = parse_rule_text(
        _doc(
            "messages: [Gearbox]\ncounter:\n  signal: ALIVE\n",
            "messages: [Brakes]\ncounter:\n  signal: ALIVE\n  max: 99\n",
        )
    )
    with pytest.raises(RuleError) as caught:
        rule_file.resolve([_message("Gearbox"), _message("Brakes")])
    assert caught.value.path == "rules.1.counter.max"


def test_an_error_in_one_rule_names_that_rule() -> None:
    rule_file = parse_rule_text(
        _doc(
            "messages: [Gearbox]\ncounter:\n  signal: ALIVE\n",
            "glob: 'B*'\nchecksum:\n  signal: CRC\n  algorithm: sum8\n  covers: [9]\n",
        )
    )
    with pytest.raises(RuleError) as caught:
        rule_file.resolve([_message("Gearbox"), _message("Brakes")])
    assert caught.value.path == "rules.1.checksum.covers.0"


def test_resolve_accepts_any_sequence_of_messages() -> None:
    rule_file = parse_rule_text(GEARBOX_RULES)
    assert set(rule_file.resolve((_message(),))) == {"Gearbox"}
    assert dict(parse_rule_text("version: 1\nrules: []\n").resolve([_message()])) == {}
    # With no messages at all, every rule matches nothing.
    with pytest.raises(RuleError, match="matches no message"):
        rule_file.resolve([])


def test_a_file_of_many_rules_resolves_against_many_messages() -> None:
    names = [f"Msg{i}" for i in range(3000)]
    rules = "".join(f"  - messages: [{', '.join(names[i::1000])}]\n    counter:\n      signal: ALIVE\n" for i in range(1000))
    rule_file = parse_rule_text("version: 1\nrules:\n" + rules)
    resolved = rule_file.resolve([_message(name) for name in names])
    assert len(resolved) == 3000


# --- The store ------------------------------------------------------------------------------------------------


def test_rule_store_round_trip_by_hash(tmp_path: Path) -> None:
    store = RuleStore(tmp_path / "rules")
    digest = store.import_text(GEARBOX_RULES)
    assert digest == hashlib.sha256(GEARBOX_RULES.encode("utf-8")).hexdigest()
    assert (tmp_path / "rules" / f"{digest}.yaml").read_bytes() == GEARBOX_RULES.encode("utf-8")
    assert store.load(digest) == parse_rule_text(GEARBOX_RULES)
    resolved = store.load(digest).resolve([_message()])
    assert [c.signal for c in resolved["Gearbox"].checksums] == ["CRC"]
    # Importing the same text again is the same artifact.
    assert store.import_text(GEARBOX_RULES) == digest
    assert [p.name for p in (tmp_path / "rules").iterdir()] == [f"{digest}.yaml"]


def test_rule_store_import_parsed_returns_the_digest_and_the_rule_file_from_one_parse(tmp_path: Path) -> None:
    store = RuleStore(tmp_path / "rules")
    digest, parsed = store.import_parsed(GEARBOX_RULES)
    assert digest == store.import_text(GEARBOX_RULES)
    assert parsed == parse_rule_text(GEARBOX_RULES) == store.load(digest)
    with pytest.raises(RuleError):
        store.import_parsed(GEARBOX_RULES + "extra: 1\n")


def test_rule_store_hashes_the_utf8_bytes_of_the_text(tmp_path: Path) -> None:
    text = GEARBOX_RULES + "# Getriebesteuergerät, 変速機\n"
    digest = RuleStore(tmp_path).import_text(text)
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert RuleStore(tmp_path).load(digest) == parse_rule_text(text)


def test_rule_store_directory_is_private(tmp_path: Path) -> None:
    directory = tmp_path / "config" / "rules"
    RuleStore(directory).import_text(GEARBOX_RULES)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    stored = next(directory.iterdir())
    assert stat.S_IMODE(stored.stat().st_mode) & 0o077 == 0


def test_an_invalid_rule_file_leaves_nothing_on_disk(tmp_path: Path) -> None:
    directory = tmp_path / "rules"
    store = RuleStore(directory)
    with pytest.raises(RuleError, match="rules.0.checksum.poly"):
        store.import_text(_doc(_checksum(algorithm="crc8", poly="0x1FF")))
    assert not directory.exists()
    # Once the directory exists, a refused file still adds nothing to it.
    digest = store.import_text(GEARBOX_RULES)
    with pytest.raises(RuleError):
        store.import_text(GEARBOX_RULES + "extra: 1\n")
    assert [p.name for p in directory.iterdir()] == [f"{digest}.yaml"]


def test_rule_store_missing_hash(tmp_path: Path) -> None:
    store = RuleStore(tmp_path)
    with pytest.raises(ValueError, match="Missing rule file"):
        store.load("0" * 64)


@pytest.mark.parametrize(
    "digest",
    ["", "abc", "A" * 64, "0" * 63, "0" * 65, "../" + "0" * 61, "g" * 64, ("0" * 63 + "/"), "0" * 64 + "\n"],
)
def test_rule_store_refuses_anything_but_a_lowercase_sha256(tmp_path: Path, digest: str) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        RuleStore(tmp_path).load(digest)


def test_rule_store_refuses_a_non_string_digest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        RuleStore(tmp_path).load(None)  # type: ignore[arg-type]


def test_rule_store_detects_a_tampered_file(tmp_path: Path) -> None:
    store = RuleStore(tmp_path)
    digest = store.import_text(GEARBOX_RULES)
    (tmp_path / f"{digest}.yaml").write_bytes(GEARBOX_RULES.replace("0x5A", "0x5B").encode("utf-8"))
    with pytest.raises(ValueError, match="hash mismatch"):
        store.load(digest)


def test_rule_store_default_directory_follows_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert RuleStore().directory == tmp_path / "xdg" / "ecu-simulator" / "rules"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert RuleStore().directory == tmp_path / "home" / ".config" / "ecu-simulator" / "rules"
    assert RuleStore().directory.parent == DbcStore().directory.parent


def test_rule_store_load_reparses_the_stored_text(tmp_path: Path) -> None:
    # A file whose bytes still hash to the digest but no longer validate (a newer reader, say) is refused on load too.
    store = RuleStore(tmp_path)
    bad = _doc(_checksum(algorithm="crc8", poly="0x1FF"))
    digest = hashlib.sha256(bad.encode("utf-8")).hexdigest()
    (tmp_path / f"{digest}.yaml").write_bytes(bad.encode("utf-8"))
    with pytest.raises(RuleError, match="rules.0.checksum.poly"):
        store.load(digest)


def test_rule_error_carries_its_path() -> None:
    error = RuleError("rules.2.glob", "matches no message")
    assert str(error) == "rules.2.glob: matches no message"
    assert error.path == "rules.2.glob"
    whole = RuleError("", "rule file is empty")
    assert str(whole) == "rule file is empty" and whole.path == ""


# --- Bounds on every integer, stated once as a named constant each ---------------------------------------------------


def test_the_bounds_are_the_documented_ones() -> None:
    assert (MAX_COVERS, MAX_COVERS_INDEX, MAX_COUNTER_MAX, MAX_DATA_ID) == (64, 63, 2**64 - 1, 255)


ACCEPTED_ON_THE_BOUND = [
    pytest.param(_checksum(algorithm="sum8", covers=f"[0, {MAX_COVERS_INDEX}]"), id="covers-63"),
    pytest.param(_checksum(algorithm="sum8", covers=f"[{', '.join(str(i) for i in range(MAX_COVERS))}]"), id="64-covers"),
    pytest.param("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: 18446744073709551615\n", id="counter-max-2-64-1"),
    pytest.param("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: 0\n", id="counter-max-0"),
    pytest.param(_checksum(algorithm="crc16", poly="0xFFFF", init="0xFFFF", xorout="0xFFFF"), id="crc16-all-ones"),
    pytest.param(_checksum(algorithm="crc16", poly=1, init=0, xorout=0), id="crc16-poly-1"),
    pytest.param(_checksum(algorithm="crc8", poly="0xFF", init="0xFF", xorout="0xFF"), id="crc8-all-ones"),
    pytest.param(_checksum(algorithm="xor", init=255, xorout=255), id="xor-255"),
    pytest.param(_checksum(algorithm="sum8", init=255, xorout=255), id="sum8-255"),
    pytest.param(_checksum(algorithm="nibble_sum", init=15, xorout=15), id="nibble-sum-15"),
    pytest.param(_checksum(algorithm="sum8", data_id=255), id="data-id-255"),
    pytest.param(_checksum(algorithm="sum8", data_id=0), id="data-id-0"),
]


@pytest.mark.parametrize("rule", ACCEPTED_ON_THE_BOUND)
def test_a_value_on_each_bound_is_accepted(rule: str) -> None:
    assert len(parse_rule_text(_doc(rule)).rules) == 1


PAST_THE_BOUND = [
    pytest.param(_checksum(algorithm="sum8", covers=f"[{MAX_COVERS_INDEX + 1}]"), "rules.0.checksum.covers.0", id="covers-64"),
    pytest.param(_checksum(algorithm="sum8", covers=f"[{', '.join(['0'] * (MAX_COVERS + 1))}]"), "rules.0.checksum.covers", id="65-covers"),
    pytest.param("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: 18446744073709551616\n", "rules.0.counter.max", id="counter-max-2-64"),
    pytest.param("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: -1\n", "rules.0.counter.max", id="counter-max-minus-1"),
    pytest.param(_checksum(algorithm="crc16", poly="0x10000"), "rules.0.checksum.poly", id="crc16-poly-17-bits"),
    pytest.param(_checksum(algorithm="crc16", poly=1, init="0x10000"), "rules.0.checksum.init", id="crc16-init-17-bits"),
    pytest.param(_checksum(algorithm="crc16", poly=1, xorout="0x10000"), "rules.0.checksum.xorout", id="crc16-xorout-17-bits"),
    pytest.param(_checksum(algorithm="crc8", poly="0x100"), "rules.0.checksum.poly", id="crc8-poly-9-bits"),
    pytest.param(_checksum(algorithm="xor", init=256), "rules.0.checksum.init", id="xor-init-256"),
    pytest.param(_checksum(algorithm="sum8", xorout=256), "rules.0.checksum.xorout", id="sum8-xorout-256"),
    pytest.param(_checksum(algorithm="nibble_sum", init=16), "rules.0.checksum.init", id="nibble-init-16"),
    pytest.param(_checksum(algorithm="sum8", data_id=256), "rules.0.checksum.data_id", id="data-id-256"),
]


@pytest.mark.parametrize(("rule", "path"), PAST_THE_BOUND)
def test_the_first_value_past_each_bound_is_refused(rule: str, path: str) -> None:
    with pytest.raises(RuleError) as caught:
        parse_rule_text(_doc(rule))
    assert caught.value.path == path
    assert str(caught.value).startswith(f"{path}: ")


def test_a_counter_max_of_2_to_64_minus_1_fits_a_64_bit_signal() -> None:
    message = _message(signals=[("WIDE", 0, 64, True)])
    rule_file = parse_rule_text(_doc("messages: [Gearbox]\ncounter:\n  signal: WIDE\n  max: 18446744073709551615\n"))
    assert rule_file.resolve([message])["Gearbox"].counters[0].max_value == MAX_COUNTER_MAX
    # The same max on a 4-bit signal is the resolve-time refusal, and its 20 digits are still shown.
    narrow = parse_rule_text(_doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: 18446744073709551615\n"))
    with pytest.raises(RuleError, match="value 18446744073709551615 does not fit the 4-bit signal ALIVE"):
        narrow.resolve([_message()])


def test_huge_hex_covers_and_counter_max_are_refused_at_parse_time_not_at_resolve() -> None:
    # Before the bounds, a 5000-digit hex covers item or counter max parsed fine and then raised a bare ValueError
    # (the interpreter's 4300-digit limit) from an f-string at resolve time, with no path.
    for text, path in [
        (_doc(_checksum(algorithm="sum8", covers=f"[{HEX_5000}]")), "rules.0.checksum.covers.0"),
        (_doc("messages: [Gearbox]\ncounter:\n  signal: ALIVE\n  max: " + HEX_5000 + "\n"), "rules.0.counter.max"),
    ]:
        with pytest.raises(RuleError) as caught:
            parse_rule_text(text)
        assert caught.value.path == path
        assert str(caught.value).startswith(f"{path}: ") and len(str(caught.value)) <= 300


def test_a_huge_integer_used_as_a_key_is_refused_with_a_short_message() -> None:
    text = "version: 1\nrules:\n  - messages: [Gearbox]\n    counter: {signal: ALIVE}\n    ? " + HEX_5000 + "\n    : 1\n"
    with pytest.raises(RuleError) as caught:
        parse_rule_text(text)
    message = str(caught.value)
    assert caught.value.path.startswith("rules.0.") and message.startswith("rules.0.")
    assert "Keys should be strings" in message or "Extra inputs are not permitted" in message
    assert len(message) <= 300 and "set_int_max_str_digits" not in message


# --- The AUTOSAR E2E presets: their fields, and the positions they fix -------------------------------------------------

# Four bytes laid out as E2E Profiles 1 and 2 require: CRC in byte 0, the counter in the low nibble of byte 1.
E2E_SIGNALS = [("CRC", 0, 8, True), ("ALIVE", 8, 4, True), ("DATA", 16, 16, True)]
P01 = "algorithm: e2e_p01\n  data_id: 0x123\n  data_id_mode: both\n"
P02_LIST = "[" + ", ".join(str(k) for k in range(16)) + "]"
P02 = f"algorithm: e2e_p02\n  data_id_list: {P02_LIST}\n"


def _e2e(checksum: str, *, counter: str = "counter:\n  signal: ALIVE\n") -> str:
    """A rule file of one E2E rule for Gearbox; ``checksum`` is the lines after ``signal: CRC``, indented by two."""
    return _doc("messages: [Gearbox]\n" + counter + "checksum:\n  signal: CRC\n  " + checksum)


def _e2e_message(signals: Sequence[tuple[str, int, int, bool]] = tuple(E2E_SIGNALS), dlc: int = 4) -> dict[str, Any]:
    return _message(signals=signals, dlc=dlc)


E2E_REJECTS = [
    pytest.param(_e2e("algorithm: e2e_p01\n  data_id_mode: both\n"), "rules.0.checksum.data_id", "e2e_p01 needs a data_id",
                 id="p01-without-data-id"),
    pytest.param(_e2e("algorithm: e2e_p01\n  data_id: 1\n"), "rules.0.checksum.data_id_mode",
                 "e2e_p01 needs a data_id_mode", id="p01-without-data-id-mode"),
    pytest.param(_e2e("algorithm: e2e_p01\n  data_id: 1\n  data_id_mode: high\n"), "rules.0.checksum.data_id_mode",
                 "'nibble'", id="p01-unknown-data-id-mode"),
    pytest.param(_e2e(P01.replace("0x123", "0x10000")), "rules.0.checksum.data_id", "less than or equal to 65535",
                 id="p01-data-id-17-bits"),
    pytest.param(_e2e(P01.replace("0x123", "-1")), "rules.0.checksum.data_id", "greater than or equal to 0",
                 id="p01-data-id-negative"),
    pytest.param(_e2e(P01.replace("0x123", HEX_5000)), "rules.0.checksum.data_id", "less than or equal to 65535",
                 id="p01-data-id-5000-digit-hex"),
    pytest.param(_e2e(P01.replace("0x123", "0x100").replace("both", "low")), "rules.0.checksum.data_id",
                 "less than or equal to 255", id="p01-low-data-id-over-8-bits"),
    pytest.param(_e2e(P01.replace("0x123", "0x1000").replace("both", "nibble")), "rules.0.checksum.data_id",
                 "less than or equal to 4095", id="p01-nibble-data-id-over-12-bits"),
    pytest.param(_e2e(P01 + "  poly: 0x1D\n"), "rules.0.checksum.poly", "e2e_p01 fixes its poly", id="p01-poly"),
    pytest.param(_e2e(P01 + "  init: 0\n"), "rules.0.checksum.init", "e2e_p01 fixes its init", id="p01-init"),
    pytest.param(_e2e(P01 + "  xorout: 0\n"), "rules.0.checksum.xorout", "e2e_p01 fixes its xorout", id="p01-xorout"),
    pytest.param(_e2e(P01 + "  reflect: false\n"), "rules.0.checksum.reflect", "e2e_p01 fixes its reflect",
                 id="p01-reflect"),
    pytest.param(_e2e(P01 + "  covers: [1, 2]\n"), "rules.0.checksum.covers", "e2e_p01 fixes the bytes it covers",
                 id="p01-covers"),
    pytest.param(_e2e(P01 + f"  data_id_list: {P02_LIST}\n"), "rules.0.checksum.data_id_list",
                 "data_id_list applies only to e2e_p02", id="p01-data-id-list"),
    pytest.param(_e2e("algorithm: e2e_p02\n"), "rules.0.checksum.data_id_list", "e2e_p02 needs a data_id_list",
                 id="p02-without-data-id-list"),
    pytest.param(_e2e("algorithm: e2e_p02\n  data_id_list: [1, 2, 3]\n"), "rules.0.checksum.data_id_list",
                 "at least 16 items", id="p02-short-data-id-list"),
    pytest.param(_e2e("algorithm: e2e_p02\n  data_id_list: [" + ", ".join(["1"] * 17) + "]\n"),
                 "rules.0.checksum.data_id_list", "at most 16 items", id="p02-long-data-id-list"),
    pytest.param(_e2e("algorithm: e2e_p02\n  data_id_list: [" + ", ".join(["1"] * 5000) + "]\n"),
                 "rules.0.checksum.data_id_list", "at most 16 items", id="p02-5000-entry-data-id-list"),
    pytest.param(_e2e(P02.replace("[0,", "[256,")), "rules.0.checksum.data_id_list.0", "less than or equal to 255",
                 id="p02-data-id-list-entry-over-8-bits"),
    pytest.param(_e2e(P02.replace("[0,", f"[{HEX_5000},")), "rules.0.checksum.data_id_list.0",
                 "less than or equal to 255", id="p02-data-id-list-entry-5000-digit-hex"),
    pytest.param(_e2e(P02.replace("[0,", "[true,")), "rules.0.checksum.data_id_list.0", "valid integer",
                 id="p02-data-id-list-bool-entry"),
    pytest.param(_e2e(P02 + "  data_id: 1\n"), "rules.0.checksum.data_id", "e2e_p02 takes a data_id_list, not a data_id",
                 id="p02-data-id"),
    pytest.param(_e2e(P02 + "  data_id_mode: both\n"), "rules.0.checksum.data_id_mode",
                 "data_id_mode applies only to e2e_p01", id="p02-data-id-mode"),
    pytest.param(_e2e(P02 + "  poly: 0x2F\n"), "rules.0.checksum.poly", "e2e_p02 fixes its poly", id="p02-poly"),
    pytest.param(_e2e(P02 + "  init: 0xFF\n"), "rules.0.checksum.init", "e2e_p02 fixes its init", id="p02-init"),
    pytest.param(_e2e(P02 + "  xorout: 0xFF\n"), "rules.0.checksum.xorout", "e2e_p02 fixes its xorout", id="p02-xorout"),
    pytest.param(_e2e(P02 + "  reflect: false\n"), "rules.0.checksum.reflect", "e2e_p02 fixes its reflect",
                 id="p02-reflect"),
    pytest.param(_e2e(P02 + "  covers: [1]\n"), "rules.0.checksum.covers", "e2e_p02 fixes the bytes it covers",
                 id="p02-covers"),
    pytest.param(_doc(_checksum(algorithm="crc8", poly=7, data_id_mode="both")), "rules.0.checksum.data_id_mode",
                 "data_id_mode applies only to e2e_p01, not crc8", id="generic-data-id-mode"),
    pytest.param(_doc(_checksum(algorithm="sum8", data_id_list=P02_LIST)), "rules.0.checksum.data_id_list",
                 "data_id_list applies only to e2e_p02, not sum8", id="generic-data-id-list"),
    pytest.param(_e2e(P01, counter=""), "rules.0", "e2e_p01 needs a counter in the low nibble of byte 1",
                 id="p01-without-a-counter"),
    pytest.param(_e2e(P02, counter=""), "rules.0", "e2e_p02 needs a counter in the low nibble of byte 1",
                 id="p02-without-a-counter"),
]


@pytest.mark.parametrize(("text", "path", "needle"), E2E_REJECTS)
def test_e2e_rejects(text: str, path: str, needle: str) -> None:
    test_rejects(text, path, needle)


def test_p01_requires_data_id_and_p02_requires_sixteen_entries() -> None:
    p01 = parse_rule_text(_e2e(P01)).rules[0].checksum
    assert p01 is not None and (p01.algorithm, p01.data_id, p01.data_id_mode) == ("e2e_p01", 0x123, "both")
    assert p01.data_id_list is None
    p02 = parse_rule_text(_e2e(P02)).rules[0].checksum
    assert p02 is not None and (p02.algorithm, p02.data_id_list, p02.data_id) == ("e2e_p02", list(range(16)), None)
    assert p02.data_id_mode is None


E2E_ACCEPTED_ON_THE_BOUND = [
    pytest.param(_e2e(P01.replace("0x123", "0xFFFF")), id="p01-both-data-id-65535"),
    pytest.param(_e2e(P01.replace("0x123", "0xFFFF").replace("both", "alt")), id="p01-alt-data-id-65535"),
    pytest.param(_e2e(P01.replace("0x123", "0xFF").replace("both", "low")), id="p01-low-data-id-255"),
    pytest.param(_e2e(P01.replace("0x123", "0xFFF").replace("both", "nibble")), id="p01-nibble-data-id-4095"),
    pytest.param(_e2e(P01.replace("0x123", "0")), id="p01-data-id-0"),
    pytest.param(_e2e(P02.replace("[0,", "[255,")), id="p02-data-id-list-entry-255"),
]


@pytest.mark.parametrize("text", E2E_ACCEPTED_ON_THE_BOUND)
def test_e2e_values_on_each_bound_are_accepted(text: str) -> None:
    assert parse_rule_text(text).resolve([_e2e_message()])["Gearbox"].checksums


E2E_RESOLVE_REJECTS = [
    pytest.param(_e2e(P01), [_e2e_message([("CRC", 16, 8, True), ("ALIVE", 8, 4, True)])], "rules.0.checksum.signal",
                 "e2e_p01 needs the checksum in byte 0 (bits 0 to 7)", id="p01-crc-in-byte-2"),
    pytest.param(_e2e(P02), [_e2e_message([("CRC", 0, 4, True), ("ALIVE", 8, 4, True)])], "rules.0.checksum.signal",
                 "e2e_p02 needs the checksum in byte 0 (bits 0 to 7)", id="p02-crc-4-bits"),
    pytest.param(_e2e(P01), [_e2e_message([("CRC", 16, 16, True), ("ALIVE", 8, 4, True)])], "rules.0.checksum.signal",
                 "e2e_p01 needs the checksum in byte 0 (bits 0 to 7)", id="p01-crc-16-bits"),
    pytest.param(_e2e(P01), [_e2e_message([("CRC", 0, 8, True), ("ALIVE", 12, 4, True)])], "rules.0.counter.signal",
                 "e2e_p01 needs the counter in the low nibble of byte 1 (bits 8 to 11)", id="p01-counter-high-nibble"),
    pytest.param(_e2e(P02), [_e2e_message([("CRC", 0, 8, True), ("ALIVE", 16, 4, True)])], "rules.0.counter.signal",
                 "e2e_p02 needs the counter in the low nibble of byte 1 (bits 8 to 11)", id="p02-counter-in-byte-2"),
    pytest.param(_e2e(P02), [_e2e_message([("CRC", 0, 8, True), ("ALIVE", 8, 8, True)])], "rules.0.counter.signal",
                 "e2e_p02 needs the counter in the low nibble of byte 1 (bits 8 to 11)", id="p02-counter-8-bits"),
    pytest.param(_e2e(P01), [_e2e_message([("CRC", 0, 8, True)], dlc=1)], "rules.0.checksum.signal",
                 "e2e_p01 needs a message of at least 2 bytes", id="p01-one-byte-message"),
    pytest.param(_e2e(P02), [_e2e_message([("CRC", 0, 8, True)], dlc=1)], "rules.0.checksum.signal",
                 "e2e_p02 needs a message of at least 2 bytes", id="p02-one-byte-message"),
    pytest.param(_e2e(P01), [_e2e_message(dlc=48)], "rules.0.checksum.signal",
                 "e2e_p01 covers at most 32 bytes", id="p01-48-byte-message"),
    pytest.param(_e2e(P01, counter="counter:\n  signal: ALIVE\n  max: 15\n"), [_e2e_message()], "rules.0.counter.max",
                 "e2e_p01 counts 0 to 14", id="p01-counter-max-15"),
    pytest.param(_e2e(P02, counter="counter:\n  signal: ALIVE\n  max: 14\n"), [_e2e_message()], "rules.0.counter.max",
                 "e2e_p02 counts 0 to 15", id="p02-counter-max-14"),
]


@pytest.mark.parametrize(("text", "messages", "path", "needle"), E2E_RESOLVE_REJECTS)
def test_e2e_resolve_rejects(text: str, messages: list[dict[str, Any]], path: str, needle: str) -> None:
    test_resolve_rejects(text, messages, path, needle)
    # rule_for refuses the same match in the same words.
    with pytest.raises(RuleError) as caught:
        parse_rule_text(text).rule_for(messages[0])
    assert caught.value.path == path and needle in str(caught.value)


def test_e2e_positions_hold_in_either_byte_order() -> None:
    # Byte 0 and the low nibble of byte 1 written Motorola style (@0): the start is the most significant bit.
    message = _e2e_message([("CRC", 7, 8, False), ("ALIVE", 11, 4, False), ("DATA", 23, 16, False)])
    user = parse_rule_text(_e2e(P01)).resolve([message])["Gearbox"]
    assert [c.signal for c in user.checksums] == ["CRC"]


def test_e2e_counters_wrap_where_the_profile_says() -> None:
    p01 = parse_rule_text(_e2e(P01)).resolve([_e2e_message()])["Gearbox"]
    assert [c.max_value for c in p01.counters] == [14]
    p02 = parse_rule_text(_e2e(P02)).resolve([_e2e_message()])["Gearbox"]
    assert [c.max_value for c in p02.counters] == [15]
    # Stating the profile's own maximum is accepted.
    stated = _e2e(P01, counter="counter:\n  signal: ALIVE\n  max: 14\n")
    assert parse_rule_text(stated).resolve([_e2e_message()])["Gearbox"].counters[0].max_value == 14
    # Profile 1 covers up to 32 bytes (DataLength <= 256 bits).
    assert parse_rule_text(_e2e(P01)).resolve([_e2e_message(dlc=32)])["Gearbox"].checksums
