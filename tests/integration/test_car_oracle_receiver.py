"""opendbc's own CANParser as an independent receiver of a whole-car run.

A Toyota car and a Honda car run through the GUI's child process on vcan0, an independent raw socket captures every
frame, and the frames are fed to opendbc's ``CANParser`` for the car's DBC. The parser recomputes each checksum with
opendbc's own functions and checks that each counter follows the previous one, so it shares no code with this
project's integrity engine. Every message the engine marks ``valid`` must be accepted frame by frame.

The Honda car is HONDA_CIVIC_BOSCH (DBC ``honda_civic_hatchback_ex_2017_can_generated``): 47 of its messages are
``valid`` and 46 carry both a counter and a checksum, two of them with 29-bit identifiers (LKAS_HUD_A and LKAS_HUD_B),
which exercises Honda's extended-identifier checksum rule. The Corolla powertrain bus has 13 ``valid`` messages, 12
with a checksum only and one (STEERING_LKA) with a 6-bit counter as well.

Hyundai is left out on purpose: opendbc's parser sets ``ignore_checksum`` and ``ignore_counter`` for
``hyundai_can_generated``, so it would accept any bytes. Hyundai classic CAN rests on the oracle replay in
tests/unit/test_car_checksums.py and tests/unit/test_car_integrity.py alone.

Needs opendbc (a dev-only dependency, pinned by the catalog's revision) and skips without it. Run it with the Python
that has opendbc installed::

    PYTHON=<python with opendbc> bash scripts/run_integration_tests.sh -k oracle_receiver -q

The two control tests change one captured frame in memory and show that the parser then reports a failure, so a
parser that accepted everything would be caught.
"""

import asyncio
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator.car.catalog import load_catalog
from ecu_simulator.gui.manager import Manager
from tests.integration.test_car_vcan import DEMO, collect, open_receiver, running

opendbc = pytest.importorskip("opendbc")
CANParser = pytest.importorskip("opendbc.can.parser").CANParser

SECONDS = 7.0
DEMO_CAR = "car:\n  platform: TOYOTA_COROLLA\n  bus: pt\n"
# A frame number well inside the capture, so the changed frame has frames before and after it.
CHANGED = 10
PLATFORMS = [
    pytest.param("TOYOTA_COROLLA", id="toyota-corolla"),
    pytest.param("HONDA_CIVIC_BOSCH", id="honda-civic-bosch"),
]
VALID_MESSAGES = {"TOYOTA_COROLLA": 13, "HONDA_CIVIC_BOSCH": 47}
Frame = tuple[int, int, bool, bytes]


@dataclass(frozen=True)
class Capture:
    car: dict[str, Any]
    frames: list[Frame]
    overflow: int


@dataclass
class Verdict:
    """What the parser made of each frame of each message it was told to track."""

    parser: Any
    accepted: dict[int, list[bool]]
    counter_fail: dict[int, list[int]]


_CAPTURES: dict[str, Capture] = {}


async def captured(platform: str, vcan: str) -> Capture:
    """The run of ``platform``, captured once for every test of this module that needs it."""
    if platform not in _CAPTURES:
        text = DEMO.read_text()
        assert DEMO_CAR in text
        manager = Manager()
        with open_receiver(vcan) as sock:
            try:
                status = await running(manager, text.replace(DEMO_CAR, f"car:\n  platform: {platform}\n  bus: pt\n"))
                frames, overflow = await asyncio.to_thread(collect, sock, SECONDS)
            finally:
                await manager.close()
        _CAPTURES[platform] = Capture(status["car"], frames, overflow)
    return _CAPTURES[platform]


def valid_messages(car: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """The transmitted messages the integrity engine marks ``valid``, by address."""
    return {m["id"]: m for m in car["messages"] if m["skipped"] is None and m["integrity"] == "valid"}


def parse(car: dict[str, Any], frames: list[Frame]) -> Verdict:
    """Feed ``frames`` in order to a CANParser for the car's opendbc DBC, one update per frame."""
    tracked = valid_messages(car)
    parser = CANParser(car["dbc"]["name"], [(address, 1000 / m["period_ms"]) for address, m in tracked.items()], bus=0)
    accepted: dict[int, list[bool]] = defaultdict(list)
    counter_fail: dict[int, list[int]] = defaultdict(list)
    for stamp, can_id, _extended, data in frames:
        if can_id not in tracked:
            continue
        updated = parser.update([(stamp, [(can_id, data, 0)])])
        accepted[can_id].append(can_id in updated)
        counter_fail[can_id].append(parser.message_states[can_id].counter_fail)
    return Verdict(parser, accepted, counter_fail)


def first_with(car: dict[str, Any], key: str) -> dict[str, Any]:
    return next(m for m in valid_messages(car).values() if m[key])


def frames_of(frames: list[Frame], address: int) -> list[int]:
    return [index for index, frame in enumerate(frames) if frame[1] == address]


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.asyncio
async def test_opendbc_parser_accepts_toyota_and_honda_run(vcan, platform):
    run = await captured(platform, vcan)
    car = run.car
    assert run.overflow == 0
    tracked = valid_messages(car)
    assert len(tracked) == VALID_MESSAGES[platform]
    ids = [(m["id"], m["extended"]) for m in car["messages"] if m["skipped"] is None]
    assert len({address for address, _ in ids}) == len(ids), "a standard and an extended id share a number"

    # The parser reads opendbc's own DBC. It must be the file the catalog was extracted from.
    name = car["dbc"]["name"]
    generated = opendbc.get_generated_dbcs()
    source = generated[name].encode() if name in generated else (Path(opendbc.DBC_PATH) / f"{name}.dbc").read_bytes()
    assert hashlib.sha256(source).hexdigest() == load_catalog().dbcs[name].source_sha256

    verdict = parse(car, run.frames)
    for address, m in tracked.items():
        label = f"{m['name']} 0x{address:X}"
        message = verdict.parser.dbc.addr_to_msg[address]
        assert message.name == m["name"], label
        # Both sides must see the same counter and checksum fields, or "accepted" would mean nothing for this message.
        assert {s.name for s in message.sigs.values() if s.type == 1} == set(m["counters"]), label
        assert {s.name for s in message.sigs.values() if s.calc_checksum} == set(m["checksums"]), label

        verdicts = verdict.accepted[address]
        assert len(verdicts) >= 5, (label, len(verdicts))
        assert all(verdicts), (label, [i for i, ok in enumerate(verdicts) if not ok][:5])
        # The parser starts from a previous counter of 0, so a first frame carrying 0 counts as one miss; every frame
        # after it must follow its predecessor, which keeps the miss count at 0.
        assert max(verdict.counter_fail[address][1:]) == 0, label
    assert verdict.parser.can_valid
    print(
        f"{platform}: {len(tracked)} valid messages, "
        f"{sum(len(v) for v in verdict.accepted.values())} frames accepted of {len(run.frames)} captured "
        f"(fewest for one message {min(len(v) for v in verdict.accepted.values())}), "
        f"{sum(1 for m in tracked.values() if m['counters'])} with a counter, "
        f"{sum(1 for m in tracked.values() if m['checksums'])} with a checksum"
    )


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.asyncio
async def test_the_parser_flags_one_corrupted_checksum(vcan, platform):
    run = await captured(platform, vcan)
    message = first_with(run.car, "checksums")
    address = message["id"]
    parser = parse(run.car, run.frames).parser
    field = parser.dbc.addr_to_msg[address].sigs[message["checksums"][0]]

    indexes = frames_of(run.frames, address)
    target = indexes[CHANGED]
    stamp, can_id, extended, data = run.frames[target]
    changed = bytearray(data)
    changed[field.lsb // 8] ^= 1 << (field.lsb % 8)
    frames = list(run.frames)
    frames[target] = (stamp, can_id, extended, bytes(changed))

    verdict = parse(run.car, frames)
    rejected = [i for i, ok in enumerate(verdict.accepted[address]) if not ok]
    print(
        f"{platform}: {message['name']} 0x{address:X} frame {CHANGED} checksum bit flipped, parser rejected {rejected}"
    )
    assert rejected == [CHANGED]
    for other, verdicts in verdict.accepted.items():
        if other != address:
            assert all(verdicts), other


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.asyncio
async def test_the_parser_flags_a_counter_that_stops_advancing(vcan, platform):
    run = await captured(platform, vcan)
    message = first_with(run.car, "counters")
    address = message["id"]
    indexes = frames_of(run.frames, address)
    frames = list(run.frames)
    stuck = run.frames[indexes[CHANGED]]
    for index in indexes[CHANGED + 1 :]:
        frames[index] = (run.frames[index][0], *stuck[1:])

    verdict = parse(run.car, frames)
    rejected = [i for i, ok in enumerate(verdict.accepted[address]) if not ok]
    print(
        f"{platform}: {message['name']} 0x{address:X} counter frozen after frame {CHANGED}, "
        f"parser rejected {len(rejected)} of {len(indexes)} frames from frame {rejected[0] if rejected else None}"
    )
    assert rejected, "the parser accepted a counter that never advances"
    assert not verdict.parser.can_valid
