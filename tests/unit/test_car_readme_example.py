"""The worked rule-file example in README.md parses, binds to its DBC and stamps the frames the README shows.

The three fenced blocks under "Rule files" in the README (``dbc``, ``yaml`` and ``text``) are read from the file, so
the README cannot drift from what the code does. The checksums are recomputed here (a bitwise CRC-8 and a plain XOR)
with no code shared with ``ecu_simulator.car``.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator.car.integrity import IntegrityState, Stamper, plan_message
from ecu_simulator.car.rules import RuleStore, parse_rule_text
from ecu_simulator.dbc import DbcStore
from tests.unit.test_car_run import frame, prepare, upload

README = Path(__file__).resolve().parents[2] / "README.md"
GEARBOX_PAYLOAD = bytes([3, 0xC4, 0x09, 0, 0, 0, 0, 0])
BRAKE_PAYLOAD = bytes([0, 0, 0x10, 0x27])


def fenced(language: str) -> str:
    section = README.read_text().split("### Rule files", 1)[1]
    found = re.search(rf"^```{language}\n(.*?)^```", section, re.M | re.S)
    assert found is not None, f"no ```{language} block under 'Rule files' in README.md"
    return found.group(1)


def crc8(data: list[int], poly: int, init: int, xorout: int) -> int:
    register = init
    for byte in data:
        register ^= byte
        for _ in range(8):
            register = ((register << 1) ^ poly if register & 0x80 else register << 1) & 0xFF
    return register ^ xorout


def gearbox_crc(frame_bytes: bytes) -> int:
    """The example's crc8: poly 0x1D, init 0xFF, xorout 0xFF over bytes 0 to 6 and then the data_id 0x5A."""
    return crc8([*frame_bytes[:7], 0x5A], 0x1D, 0xFF, 0xFF)


def brake_xor(frame_bytes: bytes) -> int:
    """The example's xor with no covers: bytes 1 to 3, every byte the checksum in byte 0 does not overlap."""
    return frame_bytes[1] ^ frame_bytes[2] ^ frame_bytes[3]


@pytest.fixture
def dbc() -> bytes:
    return fenced("dbc").encode()


def messages(dbc: bytes, tmp_path: Path) -> dict[str, dict[str, Any]]:
    store = DbcStore(tmp_path)
    loaded = store.load(store.import_file("example.dbc", dbc)["sha256"])
    return {name: loaded.message(name) for name in ("Gearbox", "Brake")}


def test_the_readme_rule_file_binds_both_messages_of_its_dbc(dbc: bytes, tmp_path: Path) -> None:
    rule_file = parse_rule_text(fenced("yaml"))
    found = messages(dbc, tmp_path)
    bound = rule_file.resolve(list(found.values()))
    assert sorted(bound) == ["Brake", "Gearbox"]
    for name, user in bound.items():
        assert plan_message(found[name], rule=None, user=user).state == IntegrityState.USER_RULE


def test_the_frames_the_readme_shows_are_what_the_rule_stamps(dbc: bytes, tmp_path: Path) -> None:
    found = messages(dbc, tmp_path)
    user = parse_rule_text(fenced("yaml")).resolve(list(found.values()))["Gearbox"]
    stamper = Stamper(plan_message(found["Gearbox"], rule=None, user=user))
    shown = {
        int(count): bytes.fromhex(hex_bytes)
        for count, hex_bytes in re.findall(r"^(\d+)\s+((?:[0-9A-F]{2} ){7}[0-9A-F]{2})$", fenced("text"), re.M)
    }
    assert sorted(shown) == [0, 1, 2, 9, 10], "the README lists the frames for ALIVE 0, 1, 2, 9 and the wrap to 0"
    stamped = [stamper.stamp(GEARBOX_PAYLOAD) for _ in range(11)]
    for count in shown:
        assert stamped[count] == shown[count], count
    for number, data in enumerate(stamped):
        assert data[6] == number % 10
        assert data[7] == gearbox_crc(data), number
        assert data[:6] == GEARBOX_PAYLOAD[:6]
    assert len({data[7] for data in stamped[:10]}) == 10


def test_the_brake_rule_xors_bytes_1_to_3_with_a_counter_that_wraps_after_15(dbc: bytes, tmp_path: Path) -> None:
    found = messages(dbc, tmp_path)
    user = parse_rule_text(fenced("yaml")).resolve(list(found.values()))["Brake"]
    stamper = Stamper(plan_message(found["Brake"], rule=None, user=user))
    stamped = [stamper.stamp(BRAKE_PAYLOAD) for _ in range(33)]
    assert [data[1] & 0xF for data in stamped] == [number % 16 for number in range(33)]
    for number, data in enumerate(stamped):
        assert data[0] == brake_xor(data), number
        assert data[2:] == BRAKE_PAYLOAD[2:]


def test_the_example_runs_as_a_car_with_a_rule_file(
    dbc: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through ``prepare_car``: an uploaded DBC and its rule file, the way a profile's ``car:`` section names them."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    DbcStore().import_file("example.dbc", dbc)
    digest = RuleStore().import_text(fenced("yaml"))
    prepared = prepare(upload(dbc, rule_file=digest))
    listed = {message["name"]: message for message in prepared.metadata["messages"]}
    for name, counter in (("Gearbox", "ALIVE"), ("Brake", "ALIVE")):
        assert listed[name]["integrity"] == "user_rule", name
        assert listed[name]["counters"] == [counter] and listed[name]["checksums"] == ["CRC"]
    gearbox, brake = frame(prepared, "Gearbox"), frame(prepared, "Brake")
    sent = [gearbox.sample() for _ in range(12)]
    assert [data[6] for data in sent] == [number % 10 for number in range(12)]
    assert all(data[7] == gearbox_crc(data) for data in sent)
    sent = [brake.sample() for _ in range(18)]
    assert [data[1] & 0xF for data in sent] == [number % 16 for number in range(18)]
    assert all(data[0] == brake_xor(data) for data in sent)
