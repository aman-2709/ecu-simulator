#!/usr/bin/env python3
"""Export comma.ai opendbc's car catalog for Toyota, Honda and Hyundai (dev-only).

Run it on a maintainer's machine against a pinned opendbc checkout, with a Python that has opendbc installed.
Nothing in the ecu_simulator package imports this script or opendbc.

    python scripts/extract_opendbc.py --opendbc <opendbc checkout> [--out src/ecu_simulator/_opendbc]

It writes only `cars.json` and the DBC files under `cars/` in the output directory. Files already in that
directory, such as the wheel-speed adapter's pinned DBC, are never touched.

A bus whose DBC is CAN FD (a message over 8 bytes, or "canfd" in the name) is dropped, because the run publisher is
Classical CAN. A platform left with no bus is listed under `skipped`.
"""

import argparse
import hashlib
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PINNED_REVISION = "af54130d51111197d34384e7dad9ace229b724e4"
RATES_SOURCE = "opendbc safety RX tables (expected frequency, approximate)"
BRANDS = ("toyota", "honda", "hyundai")
SAFETY_HEADERS = {"toyota": ("toyota.h",), "honda": ("honda.h",), "hyundai": ("hyundai.h", "hyundai_common.h")}
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src" / "ecu_simulator" / "_opendbc"

# One receive check inside a safety table: {address, bus, length, expected frequency in Hz, ...}. A transmit entry
# has no frequency field, so it does not match.
_RX_CHECK = re.compile(r"\{\s*(0x[0-9A-Fa-f]+)\s*,\s*[^,{}]+,\s*\d+\s*,\s*(\d+)U\b")
_C_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _rule(dbc_name: str) -> str | None:
    if dbc_name.startswith(("toyota_", "lexus_")):
        return "toyota"
    if dbc_name.startswith(("honda_", "acura_")):
        return "honda"
    if dbc_name == "hyundai_can_generated":
        return "hyundai_can"
    return None


def _load_opendbc(checkout: Path) -> Any:
    head = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != PINNED_REVISION:
        raise SystemExit(f"{checkout} is at {head}, expected {PINNED_REVISION}")
    dirty = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit(f"{checkout} has local changes:\n{dirty}")
    sys.path.insert(0, str(checkout))
    opendbc = importlib.import_module("opendbc")
    if Path(opendbc.__file__).resolve().parent != (checkout / "opendbc").resolve():
        raise SystemExit(f"imported opendbc from {opendbc.__file__}, not from {checkout}")
    return opendbc


def _dbc_bytes(opendbc: Any, name: str) -> bytes:
    # opendbc's own lookup order: a generated DBC wins over a file of the same name.
    generated = opendbc.get_generated_dbcs()
    if name in generated:
        return generated[name].encode("utf-8")
    return (Path(opendbc.DBC_PATH) / f"{name}.dbc").read_bytes()


def _safety_frequencies(modes_dir: Path, filenames: tuple[str, ...]) -> dict[int, set[float]]:
    found: dict[int, set[float]] = {}
    for filename in filenames:
        source = _C_COMMENT.sub("", (modes_dir / filename).read_text(encoding="utf-8"))
        for address, hertz in _RX_CHECK.findall(source):
            found.setdefault(int(address, 16), set()).add(float(int(hertz)))
    return found


def _rates(
    brand: str, frequencies: dict[int, set[float]], names_by_address: dict[int, set[str]]
) -> dict[str, float]:
    """Map message name to Hz, leaving out what the tables do not settle.

    An address is left out when it carries several frequencies or resolves to several names across the brand's
    DBCs. A name is left out when it is a candidate at such an address, since its rate there is unknown, or when
    two addresses give it different frequencies. An address with no message in the DBCs is only counted.
    """
    conflicting: list[int] = []
    ambiguous: list[int] = []
    unresolved: list[int] = []
    unsettled: set[str] = set()
    claimed: dict[str, set[float]] = {}
    for address, hertz in sorted(frequencies.items()):
        names = names_by_address.get(address, set())
        if len(hertz) > 1:
            conflicting.append(address)
            unsettled |= names
        elif not names:
            unresolved.append(address)
        elif len(names) > 1:
            ambiguous.append(address)
            unsettled |= names
        else:
            claimed.setdefault(next(iter(names)), set()).update(hertz)
    split = sorted(name for name, hertz in claimed.items() if len(hertz) > 1)
    shadowed = sorted(name for name in claimed if name in unsettled and name not in split)
    rates = {
        name: next(iter(hertz))
        for name, hertz in sorted(claimed.items())
        if len(hertz) == 1 and name not in unsettled
    }

    def show(addresses: list[int]) -> str:
        return ", ".join(f"0x{address:X}" for address in sorted(addresses)) or "-"

    print(f"rates {brand}: {len(rates)} messages from {len(frequencies)} safety addresses")
    print(f"  addresses left out, several frequencies ({len(conflicting)}): {show(conflicting)}")
    print(f"  addresses left out, ambiguous name ({len(ambiguous)}): {show(ambiguous)}")
    print(f"  addresses with no message in the shipped DBCs ({len(unresolved)}): {show(unresolved)}")
    print(f"  names left out, different frequencies at different addresses ({len(split)}): {', '.join(split) or '-'}")
    print(f"  names left out, also a candidate at a left-out address ({len(shadowed)}): {', '.join(shadowed) or '-'}")
    return rates


def build(opendbc: Any, checkout: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Return the catalog and the shipped DBC bytes by DBC name."""
    from opendbc.can.dbc import DBC
    from opendbc.car.fw_query_definitions import ECU_NAME

    candidates: list[tuple[str, str, list[str], dict[str, str], dict[Any, Any]]] = []
    queries: dict[str, list[dict[str, Any]]] = {}
    for brand in BRANDS:
        values = importlib.import_module(f"opendbc.car.{brand}.values")
        fw_versions = importlib.import_module(f"opendbc.car.{brand}.fingerprints").FW_VERSIONS
        for platform in values.CAR:
            buses = {str(bus): name for bus, name in platform.config.dbc_dict.items() if name}
            names = [doc.name for doc in platform.config.car_docs]
            candidates.append((brand, platform.name, names, buses, fw_versions.get(platform, {})))
        queries[brand] = [
            {
                "request": [chunk.hex() for chunk in request.request],
                "response": [chunk.hex() for chunk in request.response],
                "whitelist": [ECU_NAME[ecu] for ecu in request.whitelist_ecus],
                "rx_offset": request.rx_offset,
                "bus": request.bus,
            }
            for request in values.FW_QUERY_CONFIG.requests
        ]

    raw: dict[str, bytes] = {}
    messages: dict[str, list[Any]] = {}
    can_fd: set[str] = set()
    for dbc_name in sorted({name for *_, buses, _ in candidates for name in buses.values()}):
        raw[dbc_name] = _dbc_bytes(opendbc, dbc_name)
        # opendbc's own reader, not ecu_simulator's ImportedDb: cantools rejects most Honda DBCs and
        # hyundai_can_generated as published (malformed CM_ lines, 29-bit IDs without the extended flag).
        messages[dbc_name] = list(DBC(dbc_name).msgs.values())
        if "canfd" in dbc_name or any(message.size > 8 for message in messages[dbc_name]):
            can_fd.add(dbc_name)

    platforms: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    skipped_brand: dict[str, str] = {}
    for brand, platform_id, names, buses, fw in candidates:
        kept = {bus: name for bus, name in buses.items() if name not in can_fd}
        if not kept:
            skipped[platform_id] = "CAN FD"
            skipped_brand[platform_id] = brand
            continue
        platforms[platform_id] = {
            "brand": brand,
            "names": names,
            "buses": kept,
            "ecus": [
                {"role": ECU_NAME[ecu], "address": address, "subaddress": subaddress, "fw": [v.hex() for v in versions]}
                for (ecu, address, subaddress), versions in fw.items()
            ],
        }

    shipped = sorted({name for platform in platforms.values() for name in platform["buses"].values()})
    dbcs = {
        name: {
            "file": f"cars/{name}.dbc",
            "sha256": hashlib.sha256(raw[name]).hexdigest(),
            "rule": _rule(name),
        }
        for name in shipped
    }

    modes_dir = checkout / "opendbc" / "safety" / "modes"
    rates: dict[str, dict[str, float]] = {}
    for brand in BRANDS:
        names_by_address: dict[int, set[str]] = {}
        for dbc_name in sorted({n for p in platforms.values() if p["brand"] == brand for n in p["buses"].values()}):
            for message in messages[dbc_name]:
                names_by_address.setdefault(message.address, set()).add(message.name)
        rates[brand] = _rates(brand, _safety_frequencies(modes_dir, SAFETY_HEADERS[brand]), names_by_address)

    for brand in BRANDS:
        listed = sum(platform["brand"] == brand for platform in platforms.values())
        left = sum(owner == brand for owner in skipped_brand.values())
        print(f"{brand}: {listed} platforms listed, {left} skipped (CAN FD)")
    print(f"CAN FD DBCs dropped: {sorted(can_fd)}")

    catalog = {
        "revision": PINNED_REVISION,
        "rates_source": RATES_SOURCE,
        "dbcs": dbcs,
        "platforms": platforms,
        "fw_queries": queries,
        "rates_hz": rates,
        "skipped": skipped,
    }
    return catalog, {name: raw[name] for name in shipped}


def render(catalog: dict[str, Any]) -> str:
    """JSON with one top-level entry and one second-level entry per line, so a regenerated file diffs by platform."""
    lines = ["{"]
    for index, (key, value) in enumerate(catalog.items()):
        comma = "," if index < len(catalog) - 1 else ""
        if not isinstance(value, dict):
            lines.append(f"{json.dumps(key)}:{json.dumps(value)}{comma}")
            continue
        lines.append(f"{json.dumps(key)}:{{")
        for position, (name, entry) in enumerate(value.items()):
            tail = "," if position < len(value) - 1 else ""
            lines.append(f" {json.dumps(name)}:{json.dumps(entry, separators=(',', ':'))}{tail}")
        lines.append(f"}}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write(out: Path, catalog: dict[str, Any], shipped: dict[str, bytes]) -> None:
    cars = out / "cars"
    cars.mkdir(parents=True, exist_ok=True)
    for name, content in shipped.items():
        (cars / f"{name}.dbc").write_bytes(content)
    for stale in cars.glob("*.dbc"):
        if stale.stem not in shipped:
            stale.unlink()
    (out / "cars.json").write_text(render(catalog), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--opendbc", type=Path, required=True, help="opendbc checkout at the pinned revision")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory that receives cars.json and cars/")
    args = parser.parse_args(argv)
    checkout = args.opendbc.resolve()
    opendbc = _load_opendbc(checkout)
    catalog, shipped = build(opendbc, checkout)
    write(args.out, catalog, shipped)
    print(f"{len(shipped)} DBC files written to {args.out / 'cars'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
