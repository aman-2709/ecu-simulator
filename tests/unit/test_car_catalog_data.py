"""Committed opendbc catalog: cars.json and the DBC files it points to.

The data is produced once by scripts/extract_opendbc.py against a pinned opendbc checkout. These tests read only the
committed files, so they need neither opendbc nor its dependencies.
"""

import hashlib
import json
import re
from importlib.resources import files
from pathlib import Path

import pytest

REVISION = "af54130d51111197d34384e7dad9ace229b724e4"
RATES_SOURCE = "opendbc safety RX tables (expected frequency, approximate)"
BRANDS = {"toyota", "honda", "hyundai"}
MAX_DATA_BYTES = 5 * 1024 * 1024
# opendbc's own message line reader. Cantools rejects several shipped DBCs as published, so this does not use it.
BO_LINE = re.compile(r"^BO_ (\w+) (\w+) *: (\w+)", re.M)


@pytest.fixture(scope="module")
def root() -> Path:
    return Path(str(files("ecu_simulator._opendbc")))


@pytest.fixture(scope="module")
def catalog(root: Path) -> dict:
    return json.loads((root / "cars.json").read_text(encoding="utf-8"))


def messages_by_dbc(root: Path, catalog: dict) -> dict[str, list[tuple[str, int]]]:
    result = {}
    for name, entry in catalog["dbcs"].items():
        text = (root / entry["file"]).read_text(encoding="utf-8")
        result[name] = [(match.group(2), int(match.group(3), 0)) for match in BO_LINE.finditer(text)]
    return result


def test_revision_is_pinned(catalog: dict) -> None:
    assert catalog["revision"] == REVISION


def test_every_dbc_file_matches_its_sha256(root: Path, catalog: dict) -> None:
    assert catalog["dbcs"]
    for name, entry in catalog["dbcs"].items():
        assert entry["file"] == f"cars/{name}.dbc"
        assert hashlib.sha256((root / entry["file"]).read_bytes()).hexdigest() == entry["sha256"], name


def test_every_platform_bus_names_a_listed_dbc(catalog: dict) -> None:
    assert catalog["platforms"]
    for platform_id, platform in catalog["platforms"].items():
        assert platform["buses"], platform_id
        for bus, dbc in platform["buses"].items():
            assert dbc in catalog["dbcs"], f"{platform_id} {bus}"


def test_every_listed_dbc_is_used_by_a_platform(catalog: dict) -> None:
    used = {dbc for platform in catalog["platforms"].values() for dbc in platform["buses"].values()}
    assert set(catalog["dbcs"]) == used


def test_only_three_brands(catalog: dict) -> None:
    assert {platform["brand"] for platform in catalog["platforms"].values()} == BRANDS
    assert set(catalog["fw_queries"]) == BRANDS
    assert set(catalog["rates_hz"]) == BRANDS


def test_rules_assigned_by_name_prefix(catalog: dict) -> None:
    rules = {name: entry["rule"] for name, entry in catalog["dbcs"].items()}
    for name, rule in rules.items():
        if name.startswith(("toyota_", "lexus_")):
            assert rule == "toyota", name
        elif name.startswith(("honda_", "acura_")):
            assert rule == "honda", name
        elif name == "hyundai_can_generated":
            assert rule == "hyundai_can", name
        else:
            assert rule is None, name
    assert {"toyota", "honda", "hyundai_can", None} <= set(rules.values())


def test_canfd_platforms_are_skipped_not_listed(catalog: dict) -> None:
    canfd = {platform_id for platform_id, reason in catalog["skipped"].items() if reason == "CAN FD"}
    assert any(platform_id.startswith("HYUNDAI_") for platform_id in canfd)
    assert canfd.isdisjoint(catalog["platforms"])
    assert set(catalog["skipped"]).isdisjoint(catalog["platforms"])
    assert not [name for name in catalog["dbcs"] if "canfd" in name]


def test_no_shipped_dbc_has_a_message_over_8_bytes(root: Path, catalog: dict) -> None:
    for name, messages in messages_by_dbc(root, catalog).items():
        assert messages, name
        assert max(length for _, length in messages) <= 8, name


def test_corolla_has_a_powertrain_and_a_radar_bus(catalog: dict) -> None:
    corolla = catalog["platforms"]["TOYOTA_COROLLA"]
    assert corolla["brand"] == "toyota"
    assert corolla["buses"] == {"pt": "toyota_new_mc_pt_generated", "radar": "toyota_adas"}


def test_toyota_has_wheel_speeds_rate(catalog: dict) -> None:
    assert catalog["rates_hz"]["toyota"]["WHEEL_SPEEDS"] > 0


def test_rates_say_where_they_come_from(catalog: dict) -> None:
    assert catalog["rates_source"] == RATES_SOURCE


def test_no_rate_is_non_positive(catalog: dict) -> None:
    rates = [hertz for brand in catalog["rates_hz"].values() for hertz in brand.values()]
    assert rates
    assert all(isinstance(hertz, float) and hertz > 0 for hertz in rates)


def test_every_rated_message_exists_in_a_dbc_of_its_brand(root: Path, catalog: dict) -> None:
    messages = messages_by_dbc(root, catalog)
    for brand, rates in catalog["rates_hz"].items():
        known = {
            name
            for platform in catalog["platforms"].values()
            if platform["brand"] == brand
            for dbc in platform["buses"].values()
            for name, _ in messages[dbc]
        }
        assert set(rates) <= known, brand


def test_total_data_under_5_mib(root: Path) -> None:
    total = (root / "cars.json").stat().st_size + sum(path.stat().st_size for path in (root / "cars").glob("*.dbc"))
    assert 0 < total < MAX_DATA_BYTES
