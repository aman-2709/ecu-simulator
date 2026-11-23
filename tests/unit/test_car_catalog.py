"""The catalog API over the committed cars.json, and the bundled-DBC fallback in DbcStore.load."""

import dataclasses
import hashlib
import json
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from ecu_simulator.car.catalog import Catalog, DbcEntry, Platform, bundled_dbc_path, load_catalog, rule_for_sha
from ecu_simulator.dbc import DbcStore, ImportedDb

BMW_SHA256 = "c238d3938e4dc461b96b5055e790ccea01d03fc8c84d44ebeb35446654988f07"
UNKNOWN_SHA256 = "0" * 64


@pytest.fixture(scope="module")
def raw_catalog() -> dict:
    return json.loads(files("ecu_simulator").joinpath("_opendbc", "cars.json").read_text(encoding="utf-8"))


def normalised_entries() -> list[DbcEntry]:
    return [entry for entry in load_catalog().dbcs.values() if entry.source_sha256 != entry.sha256]


def test_catalog_has_corolla_with_two_buses() -> None:
    corolla = load_catalog().platforms["TOYOTA_COROLLA"]
    assert isinstance(corolla, Platform)
    assert corolla.id == "TOYOTA_COROLLA"
    assert corolla.brand == "toyota"
    assert corolla.names
    assert set(corolla.buses) == {"pt", "radar"}
    assert all(name in load_catalog().dbcs for name in corolla.buses.values())


def test_catalog_mirrors_cars_json(raw_catalog: dict) -> None:
    catalog = load_catalog()
    assert catalog.revision == raw_catalog["revision"]
    assert catalog.rates_source == raw_catalog["rates_source"]
    assert dict(catalog.skipped) == raw_catalog["skipped"]
    assert {brand: dict(rates) for brand, rates in catalog.rates_hz.items()} == raw_catalog["rates_hz"]
    assert list(catalog.dbcs) == list(raw_catalog["dbcs"])
    for name, entry in catalog.dbcs.items():
        source = raw_catalog["dbcs"][name]
        assert entry == DbcEntry(
            name, source["file"], source["sha256"], source["source_sha256"], tuple(source["normalised"]), source["rule"]
        )
    assert list(catalog.platforms) == list(raw_catalog["platforms"])
    for platform_id, platform in catalog.platforms.items():
        source = raw_catalog["platforms"][platform_id]
        assert (platform.id, platform.brand) == (platform_id, source["brand"])
        assert platform.names == tuple(source["names"])
        assert dict(platform.buses) == source["buses"]


def test_load_catalog_is_cached() -> None:
    assert load_catalog() is load_catalog()


def test_catalog_cannot_be_changed_by_callers() -> None:
    catalog = load_catalog()
    platform = catalog.platforms["TOYOTA_COROLLA"]
    entry = catalog.dbcs["toyota_nodsu_pt_generated"]
    brand = next(iter(catalog.rates_hz))
    for frozen, attribute in ((catalog, "revision"), (platform, "id"), (entry, "name")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(frozen, attribute, "x")
    for mapping in (catalog.dbcs, catalog.platforms, catalog.skipped, catalog.rates_hz, catalog.rates_hz[brand],
                    platform.buses):
        with pytest.raises(TypeError):
            mapping["new"] = None
        with pytest.raises(TypeError):
            del mapping[next(iter(mapping))]
    assert isinstance(platform.names, tuple)
    assert isinstance(entry.normalised, tuple)


def test_rule_for_shipped_hash_of_every_dbc() -> None:
    for entry in load_catalog().dbcs.values():
        assert rule_for_sha(entry.sha256) == entry.rule, entry.name
    assert rule_for_sha(load_catalog().dbcs["toyota_nodsu_pt_generated"].sha256) == "toyota"


def test_rule_for_source_hash_of_a_normalised_dbc() -> None:
    normalised = normalised_entries()
    assert normalised
    for entry in normalised:
        assert rule_for_sha(entry.source_sha256) == entry.rule, entry.name
    assert any(entry.rule is not None for entry in normalised)


def test_bundled_path_names_the_bytes_that_hash_to_it() -> None:
    for entry in load_catalog().dbcs.values():
        path = bundled_dbc_path(entry.sha256)
        assert isinstance(path, Path), entry.name
        assert path.name == Path(entry.file).name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry.sha256, entry.name


def test_bundled_path_only_for_shipped_hash() -> None:
    normalised = normalised_entries()
    assert normalised
    for entry in normalised:
        assert bundled_dbc_path(entry.source_sha256) is None, entry.name
        assert bundled_dbc_path(entry.sha256) is not None, entry.name


def test_unknown_sha_has_no_rule_and_no_path() -> None:
    assert rule_for_sha(UNKNOWN_SHA256) is None
    assert bundled_dbc_path(UNKNOWN_SHA256) is None
    assert rule_for_sha("") is None
    assert bundled_dbc_path("not a digest") is None


def test_dbcstore_loads_bundled_car_dbc_without_import(tmp_path: Path) -> None:
    entry = load_catalog().dbcs["toyota_nodsu_pt_generated"]
    db = DbcStore(tmp_path).load(entry.sha256)
    assert isinstance(db, ImportedDb)
    assert db.sha256 == entry.sha256
    assert db.message("WHEEL_SPEEDS")["id"] == 0xAA
    assert list(tmp_path.iterdir()) == []


def test_dbcstore_loads_a_normalised_bundled_dbc(tmp_path: Path) -> None:
    entry = next(entry for entry in normalised_entries() if entry.rule == "honda")
    assert DbcStore(tmp_path).load(entry.sha256).sha256 == entry.sha256


def test_dbcstore_does_not_load_a_source_hash(tmp_path: Path) -> None:
    entry = normalised_entries()[0]
    with pytest.raises(ValueError, match="Missing DBC artifact"):
        DbcStore(tmp_path).load(entry.source_sha256)


def test_imported_copy_wins_over_the_bundle(tmp_path: Path) -> None:
    entry = load_catalog().dbcs["toyota_nodsu_pt_generated"]
    (tmp_path / f"{entry.sha256}.dbc").write_bytes(b"altered")
    with pytest.raises(ValueError, match="Stored DBC hash mismatch"):
        DbcStore(tmp_path).load(entry.sha256)


def test_bundled_bytes_are_still_hash_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    altered = tmp_path / "altered.dbc"
    altered.write_bytes(b"altered")
    monkeypatch.setattr("ecu_simulator.dbc.bundled_dbc_path", lambda sha256: altered)
    with pytest.raises(ValueError, match="Stored DBC hash mismatch"):
        DbcStore(tmp_path / "store").load(UNKNOWN_SHA256)


def test_unknown_sha_is_still_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Missing DBC artifact"):
        DbcStore(tmp_path).load(UNKNOWN_SHA256)


def test_existing_bmw_bundle_still_loads(tmp_path: Path) -> None:
    assert DbcStore(tmp_path).load(BMW_SHA256).sha256 == BMW_SHA256


def run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("code", [
    "import ecu_simulator.dbc; import ecu_simulator.car.catalog",
    "import ecu_simulator.car.catalog; import ecu_simulator.dbc",
])
def test_catalog_and_dbc_import_in_either_order(code: str) -> None:
    result = run_python(code)
    assert result.returncode == 0, result.stderr


def test_catalog_is_a_leaf_module() -> None:
    result = run_python(
        "import sys; import ecu_simulator.car.catalog; "
        "print(sorted(m for m in sys.modules if m.startswith('ecu_simulator')))"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['ecu_simulator', 'ecu_simulator.car', 'ecu_simulator.car.catalog']"


def test_catalog_exports_the_named_types() -> None:
    assert dataclasses.is_dataclass(Catalog)
    assert [field.name for field in dataclasses.fields(Catalog)] == [
        "revision", "dbcs", "platforms", "skipped", "rates_hz", "rates_source",
    ]
    assert [field.name for field in dataclasses.fields(DbcEntry)] == [
        "name", "file", "sha256", "source_sha256", "normalised", "rule",
    ]
    assert [field.name for field in dataclasses.fields(Platform)] == ["id", "brand", "names", "buses"]
