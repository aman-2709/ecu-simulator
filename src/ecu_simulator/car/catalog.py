"""Read-only view of the committed opendbc catalog (``_opendbc/cars.json``) and its DBC files.

The data is produced once by scripts/extract_opendbc.py and ships in the wheel. This module is a leaf: it imports only
the standard library, because ecu_simulator.dbc and ecu_simulator.config.schema both import it.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class DbcEntry:
    """One DBC file shipped in the wheel.

    ``sha256`` hashes the shipped bytes. ``source_sha256`` hashes opendbc's original file and equals ``sha256`` when
    nothing was normalised; ``normalised`` lists what changed.
    """

    name: str
    file: str
    sha256: str
    source_sha256: str
    normalised: tuple[str, ...]
    rule: str | None


@dataclass(frozen=True, slots=True)
class Platform:
    """A car platform and the DBC that describes each of its CAN buses (bus name -> DbcEntry name)."""

    id: str
    brand: str
    names: tuple[str, ...]
    buses: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Catalog:
    revision: str
    dbcs: Mapping[str, DbcEntry]
    platforms: Mapping[str, Platform]
    skipped: Mapping[str, str]
    rates_hz: Mapping[str, Mapping[str, float]]
    rates_source: str


def _root() -> Path:
    # The wheel installs the package as plain files, so the traversable is a filesystem path.
    return Path(str(files("ecu_simulator") / "_opendbc"))


@cache
def load_catalog() -> Catalog:
    raw = json.loads((_root() / "cars.json").read_text(encoding="utf-8"))
    dbcs = {
        name: DbcEntry(
            name=name,
            file=entry["file"],
            sha256=entry["sha256"],
            source_sha256=entry["source_sha256"],
            normalised=tuple(entry["normalised"]),
            rule=entry["rule"],
        )
        for name, entry in raw["dbcs"].items()
    }
    platforms = {
        platform_id: Platform(
            id=platform_id,
            brand=platform["brand"],
            names=tuple(platform["names"]),
            buses=MappingProxyType(dict(platform["buses"])),
        )
        for platform_id, platform in raw["platforms"].items()
    }
    return Catalog(
        revision=raw["revision"],
        dbcs=MappingProxyType(dbcs),
        platforms=MappingProxyType(platforms),
        skipped=MappingProxyType(dict(raw["skipped"])),
        rates_hz=MappingProxyType({brand: MappingProxyType(dict(rates)) for brand, rates in raw["rates_hz"].items()}),
        rates_source=raw["rates_source"],
    )


def rule_for_sha(sha256: str) -> str | None:
    """The integrity rule (counter and checksum rule) of the catalog DBC with this digest, or None.

    Matches the shipped bytes and opendbc's original bytes alike, so an operator who uploads the original file gets the
    same rule as the bundled copy.
    """
    for entry in load_catalog().dbcs.values():
        if sha256 in (entry.sha256, entry.source_sha256):
            return entry.rule
    return None


def bundled_dbc_path(sha256: str) -> Path | None:
    """The bundled file whose bytes hash to ``sha256``, or None.

    Only the shipped digest matches: a differing ``source_sha256`` names bytes that are not in the wheel.
    """
    for entry in load_catalog().dbcs.values():
        if entry.sha256 == sha256:
            return _root() / entry.file
    return None
