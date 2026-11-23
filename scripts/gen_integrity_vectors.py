#!/usr/bin/env python3
"""Generate the oracle vectors for the counter and checksum engine from opendbc's own packer (dev-only).

Run it on a maintainer's machine against the pinned opendbc checkout, with a Python that has both opendbc and
ecu_simulator installed. Nothing in the ecu_simulator package imports this script or opendbc.

    python scripts/gen_integrity_vectors.py --opendbc <opendbc checkout> [--out <fixture file>]

The fixture is tests/fixtures/car/integrity_vectors.json, which the unit tests read.

For every message in the shipped DBCs that opendbc's packer gives a counter or a checksum, it records the message's
integrity fields as opendbc's parser reads them, a base frame, and the frames `CANPacker.pack` returns on consecutive
calls. A fresh packer per vector starts the counter at 0. Layouts (rule, address, size and integrity fields) are written
once, from the first DBC by name; `dbcs` lists every DBC that shares one, where the message may be named differently.

Each layout is written as variant 0 and as variants 1 to 3 with other payloads. Variant 0 makes `max + 3` calls (3 when
there is no counter), so it shows the counter wrapping and the cycle repeating. A variant makes only 3 calls, counter
values 0, 1 and 2 reduced modulo `max + 1`. Several payloads keep a checksum that is constant for one payload, such as
Hyundai 0x386's, from passing unchecked.

Which signals count: the packer's own test. A counter is a signal of counter type or one named COUNTER, a checksum is a
signal with a checksum function. The `rule` label is only the catalog's label for the DBC (null for a DBC with no rule,
whose packer still counts a signal named COUNTER).

The non-integrity signals take fixed-seed raw values, `random.Random(address)` for variant 0 and
`random.Random(address * 1000 + variant)` for the others, passed to the packer as the physical value
`raw * factor + offset`, so rounding never hits a tie. A variant whose payload repeats an earlier variant's is drawn
again from the same stream, up to 64 times; a message with one free bit has only two payloads, and the run lists the
layouts left with a repeated payload. Counter and checksum signals are never passed in, so the
packer counts and checksums them itself. The run fails unless the frames agree with that account: the base frame holds
the seeded values, differs from every frame only in the integrity fields, and frame k holds counter `k mod (max + 1)`.

The output is deterministic: sorted keys, fixed seeds, no timestamps, one vector per line.
"""

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from extract_opendbc import PINNED_REVISION, _load_opendbc

from ecu_simulator.car.catalog import load_catalog

VARIANTS = 4
PAYLOAD_REDRAWS = 64
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "car" / "integrity_vectors.json"

RECIPE = f"""\
scratch environment (outside git; <env> is any empty directory, for example one under the ignored .superpowers/):

  git clone https://github.com/commaai/opendbc <env>/checkout     # a full clone: a shallow one cannot check out the pin
  git -C <env>/checkout checkout {PINNED_REVISION}
  uv venv --python 3.12 <env>/venv
  uv pip install --python <env>/venv/bin/python -e <env>/checkout -e <this repo>
  <env>/venv/bin/python scripts/gen_integrity_vectors.py --opendbc <env>/checkout

The run refuses to start unless the checkout is at the pinned revision with no tracked changes and the imported opendbc
comes from it. Repository tests read the committed fixture and never need this environment.
"""


def _field(signal: Any) -> dict[str, Any]:
    return {
        "signal": signal.name,
        "start": signal.start_bit,
        "size": signal.size,
        "little_endian": signal.is_little_endian,
    }


def _counter_field(signal: Any) -> dict[str, Any]:
    return {**_field(signal), "shift": signal.counter_shift, "max": signal.max_counter}


def _mask(signal: Any, size: int) -> int:
    """The message bits a signal covers, as opendbc's own writer places them."""
    from opendbc.can.packer import set_value

    covered = bytearray(size)
    set_value(covered, signal, (1 << signal.size) - 1)
    return int.from_bytes(covered, "little")


def _seeded_raw(rng: random.Random, signal: Any) -> int:
    raw = rng.getrandbits(signal.size)
    if signal.is_signed and raw >> (signal.size - 1):
        raw -= 1 << signal.size
    return raw


def _seed(address: int, variant: int) -> int:
    return address if variant == 0 else address * 1000 + variant


def _vector(
    dbc_name: str, rule: str | None, message: Any, variant: int = 0, taken: frozenset[str] | set[str] = frozenset()
) -> dict[str, Any] | None:
    """One variant of one message's vector, or None when the packer gives the message neither counter nor checksum.

    Variant 0 packs the whole counter cycle and a bit more; the others pack another payload for the first 3 calls.
    A payload whose base frame is in `taken` (the layout's earlier variants) is drawn again from the same seeded
    stream, up to PAYLOAD_REDRAWS times, so a message with few free bits still gets different payloads where it can.
    """
    from opendbc.can.dbc import SignalType
    from opendbc.can.packer import CANPacker, set_value

    signals = list(message.sigs.values())
    # The packer's own test for a counter, and its test for a checksum.
    counters = [s for s in signals if s.type == SignalType.COUNTER or s.name == "COUNTER"]
    checksums = [s for s in signals if s.calc_checksum]
    if not counters and not checksums:
        return None
    integrity = {s.name for s in [*counters, *checksums]}
    label = f"{dbc_name} {message.name} 0x{message.address:X} variant {variant}"

    masks = {s.name: _mask(s, message.size) for s in signals}
    integrity_bits = 0
    for name in integrity:
        integrity_bits |= masks[name]
    period = (counters[0].max_counter if counters else 0) + 1
    calls = period + 2 if variant == 0 else 3
    rng = random.Random(_seed(message.address, variant))
    for _ in range(PAYLOAD_REDRAWS + 1):
        raw = {s.name: _seeded_raw(rng, s) for s in signals if s.name not in integrity}
        values = {s.name: raw[s.name] * s.factor + s.offset for s in signals if s.name in raw}
        packer = CANPacker(dbc_name)
        frames = [bytes(packer.pack(message.address, values)) for _ in range(calls)]
        if any(len(frame) != message.size for frame in frames):
            raise SystemExit(f"{label}: the packer returned a frame that is not {message.size} bytes")
        base = bytearray(frames[0])
        for signal in [*counters, *checksums]:
            set_value(base, signal, 0)
        if bytes(base).hex() not in taken:
            break

    base_int = int.from_bytes(base, "little")
    for k, frame in enumerate(frames):
        if (int.from_bytes(frame, "little") ^ base_int) & ~integrity_bits:
            raise SystemExit(f"{label}: frame {k} differs from the base outside the counter and checksum fields")
        held = sum(s.get_raw_value(frame) << s.counter_shift for s in counters)
        if held != k % period:
            raise SystemExit(f"{label}: frame {k} holds counter {held}, expected {k % period}")
    for signal in signals:
        # Signals that share bits with another one cannot be read back; the packer wrote them in DBC order.
        shared = any(masks[signal.name] & masks[other.name] for other in signals if other is not signal)
        seeded = raw[signal.name] & ((1 << signal.size) - 1) if signal.name in raw else None
        if seeded is not None and not shared and signal.get_raw_value(base) != seeded:
            raise SystemExit(f"{label}: {signal.name} did not take its seeded value {seeded}")

    return {
        "rule": rule,
        "dbc": dbc_name,
        "dbcs": [dbc_name],
        "address": message.address,
        "extended": message.address > 0x7FF,
        "message": message.name,
        "size": message.size,
        "base_hex": bytes(base).hex(),
        "counters": [_counter_field(s) for s in counters],
        "checksums": [_field(s) for s in checksums],
        "frames_hex": [frame.hex() for frame in frames],
        "variant": variant,
    }


def build() -> list[dict[str, Any]]:
    """The vectors of every shipped DBC, deduplicated by rule, address and layout, in DBC name then address order.

    Each layout comes with its variants, from the first DBC by name that has it.
    """
    from opendbc.can.dbc import DBC

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for dbc_name, entry in sorted(load_catalog().dbcs.items()):
        for address, message in sorted(DBC(dbc_name).msgs.items()):
            vector = _vector(dbc_name, entry.rule, message)
            if vector is None:
                continue
            key = (
                vector["rule"],
                address,
                vector["size"],
                json.dumps(vector["counters"], sort_keys=True),
                json.dumps(vector["checksums"], sort_keys=True),
            )
            if key in groups:
                groups[key][0]["dbcs"].append(dbc_name)
                continue
            groups[key] = [vector]
            for variant in range(1, VARIANTS):
                taken = {earlier["base_hex"] for earlier in groups[key]}
                groups[key].append(_vector(dbc_name, entry.rule, message, variant, taken))
    for group in groups.values():
        for vector in group[1:]:
            vector["dbcs"] = group[0]["dbcs"]
    return [vector for group in groups.values() for vector in group]


def render(vectors: list[dict[str, Any]]) -> str:
    """A JSON list with one vector per line, so a regenerated file diffs by message."""
    lines = [json.dumps(vector, sort_keys=True, separators=(",", ":")) for vector in vectors]
    return "[\n" + ",\n".join(lines) + "\n]\n"


def summary(vectors: list[dict[str, Any]], size: int) -> str:
    groups: list[list[dict[str, Any]]] = []
    for vector in vectors:
        if vector["variant"] == 0:
            groups.append([])
        groups[-1].append(vector)
    layouts = [group[0] for group in groups]
    by_rule: dict[str, int] = {}
    for vector in layouts:
        by_rule[str(vector["rule"])] = by_rule.get(str(vector["rule"]), 0) + 1
    no_counter = sum(not vector["counters"] for vector in layouts)
    no_checksum = sum(not vector["checksums"] for vector in layouts)
    many_checksums = sum(len(vector["checksums"]) > 1 for vector in layouts)
    frames = sum(len(vector["frames_hex"]) for vector in vectors)
    repeated = [
        f"{group[0]['rule']} 0x{group[0]['address']:X} {group[0]['message']} "
        f"({len({vector['base_hex'] for vector in group})} payloads)"
        for group in groups
        if len({vector["base_hex"] for vector in group}) < len(group)
    ]
    return (
        f"{len(layouts)} layouts, {len(vectors)} vectors, {frames} frames, {size} bytes\n"
        f"  layouts per rule: {', '.join(f'{rule} {count}' for rule, count in sorted(by_rule.items()))}\n"
        f"  layouts without a counter: {no_counter}; without a checksum: {no_checksum}; "
        f"with several checksums: {many_checksums}\n"
        f"  layouts with a repeated payload: {', '.join(repeated) or 'none'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog=RECIPE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--opendbc", type=Path, required=True, help="opendbc checkout at the pinned revision")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the fixture file to write")
    args = parser.parse_args(argv)
    _load_opendbc(args.opendbc.resolve())
    vectors = build()
    text = render(vectors)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(summary(vectors, len(text.encode("utf-8"))))
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
