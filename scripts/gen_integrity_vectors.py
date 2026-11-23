#!/usr/bin/env python3
"""Generate the oracle vectors for the counter and checksum engine from opendbc's own packer (dev-only).

Run it on a maintainer's machine against the pinned opendbc checkout, with a Python that has both opendbc and
ecu_simulator installed. Nothing in the ecu_simulator package imports this script or opendbc.

    python scripts/gen_integrity_vectors.py --opendbc <opendbc checkout> [--out <fixture file>]

The fixture is tests/fixtures/car/integrity_vectors.json, which the unit tests read.

For every message in the shipped DBCs that opendbc's packer gives a counter or a checksum, it records the message's
integrity fields as opendbc's parser reads them, a base frame, and the frames `CANPacker.pack` returns on consecutive
calls. A fresh packer per vector starts the counter at 0, and the call count is `max + 3` (3 when there is no counter),
so every vector shows the counter wrapping and the cycle repeating. Vectors with the same rule, address and field layout
are written once, from the first DBC by name; `dbcs` lists every DBC that shares them, where the message may be named
differently.

Which signals count: the packer's own test. A counter is a signal of counter type or one named COUNTER, a checksum is a
signal with a checksum function. The `rule` label is only the catalog's label for the DBC (null for a DBC with no rule,
whose packer still counts a signal named COUNTER).

The non-integrity signals take fixed-seed raw values (`random.Random(address)`), passed to the packer as the physical
value `raw * factor + offset`, so rounding never hits a tie. Counter and checksum signals are never passed in, so the
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


def _vector(dbc_name: str, rule: str | None, message: Any) -> dict[str, Any] | None:
    """The vector for one message, or None when the packer gives it neither a counter nor a checksum."""
    from opendbc.can.dbc import SignalType
    from opendbc.can.packer import CANPacker, set_value

    signals = list(message.sigs.values())
    # The packer's own test for a counter, and its test for a checksum.
    counters = [s for s in signals if s.type == SignalType.COUNTER or s.name == "COUNTER"]
    checksums = [s for s in signals if s.calc_checksum]
    if not counters and not checksums:
        return None
    integrity = {s.name for s in [*counters, *checksums]}
    label = f"{dbc_name} {message.name} 0x{message.address:X}"

    masks = {s.name: _mask(s, message.size) for s in signals}
    rng = random.Random(message.address)
    raw = {s.name: _seeded_raw(rng, s) for s in signals if s.name not in integrity}
    values = {s.name: raw[s.name] * s.factor + s.offset for s in signals if s.name in raw}

    period = (counters[0].max_counter if counters else 0) + 1
    packer = CANPacker(dbc_name)
    frames = [bytes(packer.pack(message.address, values)) for _ in range(period + 2)]
    if any(len(frame) != message.size for frame in frames):
        raise SystemExit(f"{label}: the packer returned a frame that is not {message.size} bytes")

    base = bytearray(frames[0])
    for signal in [*counters, *checksums]:
        set_value(base, signal, 0)
    integrity_bits = 0
    for name in integrity:
        integrity_bits |= masks[name]
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
    }


def build() -> list[dict[str, Any]]:
    """The vectors of every shipped DBC, deduplicated by rule, address and layout, in DBC name then address order."""
    from opendbc.can.dbc import DBC

    vectors: dict[tuple[Any, ...], dict[str, Any]] = {}
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
            if key in vectors:
                vectors[key]["dbcs"].append(dbc_name)
            else:
                vectors[key] = vector
    return list(vectors.values())


def render(vectors: list[dict[str, Any]]) -> str:
    """A JSON list with one vector per line, so a regenerated file diffs by message."""
    lines = [json.dumps(vector, sort_keys=True, separators=(",", ":")) for vector in vectors]
    return "[\n" + ",\n".join(lines) + "\n]\n"


def summary(vectors: list[dict[str, Any]], size: int) -> str:
    by_rule: dict[str, int] = {}
    for vector in vectors:
        by_rule[str(vector["rule"])] = by_rule.get(str(vector["rule"]), 0) + 1
    no_counter = sum(not vector["counters"] for vector in vectors)
    no_checksum = sum(not vector["checksums"] for vector in vectors)
    many_checksums = sum(len(vector["checksums"]) > 1 for vector in vectors)
    frames = sum(len(vector["frames_hex"]) for vector in vectors)
    return (
        f"{len(vectors)} vectors, {frames} frames, {size} bytes\n"
        f"  per rule: {', '.join(f'{rule} {count}' for rule, count in sorted(by_rule.items()))}\n"
        f"  without a counter: {no_counter}; without a checksum: {no_checksum}; "
        f"with several checksums: {many_checksums}"
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
