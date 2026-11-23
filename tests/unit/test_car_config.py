"""The `car:` profile section: one whole car from the bundled catalog or from an uploaded DBC.

This is project input validation. A profile either names a catalog platform (and one of its CAN
buses) or points at an uploaded DBC by digest, never both, and never together with the older
`restbus:` or `dbc:` sections.
"""

import fnmatch
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from ecu_simulator import app
from ecu_simulator.car.catalog import load_catalog
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import ConfigError, load_profile, load_yaml, parse_profile
from ecu_simulator.dtc import DtcStore
from ecu_simulator.gui.profiles import Profiles
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.obd import ObdProtocol
from ecu_simulator.protocols.obd.protocol import VIN_LENGTH

PROFILES = default_profile_path().parent
DEMO = PROFILES / "car_toyota_demo.yaml"
UPLOAD_SHA = "ab12" * 16
OTHER_SHA = "0f" * 32
UPLOAD = {"filename": "example.dbc", "sha256": UPLOAD_SHA}


def base() -> dict:
    """The wheel-speed demo without its restbus section: a valid profile with no bus source."""
    data = load_yaml(PROFILES / "wheel_speed_demo.yaml")
    del data["restbus"]
    return data


def with_car(**car) -> dict:
    data = base()
    data["car"] = car
    return data


def refusal(data: dict) -> str:
    with pytest.raises(ConfigError) as info:
        parse_profile(data)
    return str(info.value)


def a_period_map(name: str, value) -> dict:
    return {"platform": "TOYOTA_COROLLA", "periods": {name: value}}


def mode09_vin_reply(profile) -> bytes:
    """What a scan tool reads for OBD service 09 PID 02 from a simulator running this profile."""
    vehicle = app.build_vehicle(app.RuntimeConfig.build(profile))
    protocol = ObdProtocol(vehicle, ecu_name="ECU_SIMULATOR", dtcs=DtcStore([]))
    reply = protocol.handle(ServiceRequest(bytes.fromhex("0902")))
    assert reply is not None
    return reply


# --- the source: a catalog platform or an upload ---------------------------------------------


def test_car_needs_platform_or_upload():
    assert parse_profile(with_car(platform="TOYOTA_COROLLA")).car.platform == "TOYOTA_COROLLA"
    upload = parse_profile(with_car(**UPLOAD)).car
    assert (upload.filename, upload.sha256, upload.platform) == ("example.dbc", UPLOAD_SHA, None)

    message = refusal(with_car(bus="pt"))
    assert "platform" in message and "sha256 together with filename" in message


def test_both_sources_and_neither_are_refused():
    for car in ({}, {"bus": "pt"}):
        message = refusal(with_car(**car))
        assert "car:" in message
        assert "platform" in message and "sha256 together with filename" in message

    for car in (
        {"platform": "TOYOTA_COROLLA", **UPLOAD},
        {"platform": "TOYOTA_COROLLA", "sha256": UPLOAD_SHA},
        {"platform": "TOYOTA_COROLLA", "filename": "example.dbc"},
    ):
        message = refusal(with_car(**car))
        assert "platform" in message and "sha256 together with filename" in message and "not both" in message


def test_upload_needs_filename_and_sha256_together():
    only_sha = refusal(with_car(sha256=UPLOAD_SHA))
    assert "car.filename" in only_sha and "together" in only_sha
    only_name = refusal(with_car(filename="example.dbc"))
    assert "car.sha256" in only_name and "together" in only_name
    assert parse_profile(with_car(**UPLOAD)).car is not None


def test_unknown_car_keys_are_refused():
    message = refusal(with_car(platform="TOYOTA_COROLLA", colour="red"))
    assert "car.colour" in message and "Extra inputs are not permitted" in message


def test_unknown_platform_lists_known_ones():
    message = refusal(with_car(platform="NOT_A_CAR"))
    assert "car.platform" in message and "'NOT_A_CAR'" in message
    assert ", ".join(sorted(load_catalog().platforms)) in message
    assert "unknown platform" in message
    assert refusal(with_car(platform="toyota_corolla")).count("unknown platform") == 1


def test_skipped_platform_has_its_own_message():
    skipped = load_catalog().skipped
    platform = sorted(skipped)[0]
    message = refusal(with_car(platform=platform))
    assert f"{platform!r} is not supported" in message
    assert skipped[platform] in message
    assert "unknown platform" not in message and "TOYOTA_COROLLA" not in message


# --- the bus -----------------------------------------------------------------------------------


def test_unknown_bus_for_platform():
    buses = load_catalog().platforms["TOYOTA_COROLLA"].buses
    assert sorted(buses) == ["pt", "radar"]
    assert parse_profile(with_car(platform="TOYOTA_COROLLA", bus="radar")).car.bus == "radar"

    message = refusal(with_car(platform="TOYOTA_COROLLA", bus="body"))
    assert "car.bus" in message and "'body'" in message and "pt, radar" in message

    # The bus is the platform's own: a bus another platform has is not this platform's.
    assert parse_profile(with_car(platform="HONDA_CRV_5G", bus="body")).car.bus == "body"
    assert "car.bus" in refusal(with_car(platform="TOYOTA_COROLLA", bus=""))


def test_the_bus_defaults_to_pt():
    assert parse_profile(with_car(platform="TOYOTA_COROLLA")).car.bus == "pt"
    assert parse_profile(with_car(**UPLOAD)).car.bus == "pt"


def test_upload_accepts_only_the_default_bus():
    assert parse_profile(with_car(bus="pt", **UPLOAD)).car.bus == "pt"
    message = refusal(with_car(bus="radar", **UPLOAD))
    assert "car.bus" in message and "'radar'" in message and "one DBC" in message


def test_a_bad_platform_does_not_hide_behind_a_bus_message():
    message = refusal(with_car(platform="NOT_A_CAR", bus="body"))
    assert "unknown platform" in message and "car.bus" not in message


# --- the upload ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["a/b.dbc", "a\\b.dbc", "/etc/passwd", "..\\x.dbc", "bad\nname.dbc", "bad\x00name.dbc", "tab\there.dbc", "\x7f.dbc"],
)
def test_filename_rejects_paths_and_control_characters(name):
    message = refusal(with_car(filename=name, sha256=UPLOAD_SHA))
    assert "car.filename" in message


@pytest.mark.parametrize("name", ["", "x" * 256])
def test_filename_rejects_empty_and_overlong_names(name):
    assert "car.filename" in refusal(with_car(filename=name, sha256=UPLOAD_SHA))


@pytest.mark.parametrize("name", ["x.dbc", "my car (2019).dbc", "x" * 255, "schönes_auto.dbc"])
def test_filename_accepts_plain_names(name):
    assert parse_profile(with_car(filename=name, sha256=UPLOAD_SHA)).car.filename == name


@pytest.mark.parametrize(
    "digest",
    ["xyz", "", "A" * 64, "a" * 63, "a" * 65, ("a" * 64) + "\n", "g" * 64, " " + "a" * 63],
)
def test_sha256_and_rule_file_must_be_64_lowercase_hex(digest):
    assert "car.sha256" in refusal(with_car(filename="x.dbc", sha256=digest))
    assert "car.rule_file" in refusal(with_car(platform="TOYOTA_COROLLA", rule_file=digest))


def test_rule_and_rule_file_validation():
    ok = parse_profile(with_car(platform="TOYOTA_COROLLA", rule="honda", rule_file=OTHER_SHA)).car
    assert (ok.rule, ok.rule_file) == ("honda", OTHER_SHA)
    for rule in ("toyota", "honda", "hyundai_can", None):
        assert parse_profile(with_car(platform="TOYOTA_COROLLA", rule=rule)).car.rule == rule
    none_given = parse_profile(with_car(platform="TOYOTA_COROLLA")).car
    assert (none_given.rule, none_given.rule_file) == (None, None)

    for bad in ("mazda", "Toyota", "", "hyundai"):
        assert "car.rule" in refusal(with_car(platform="TOYOTA_COROLLA", rule=bad))
    assert "car.rule_file" in refusal(with_car(platform="TOYOTA_COROLLA", rule_file="xyz"))
    assert "car.rule_file" in refusal(with_car(platform="TOYOTA_COROLLA", rule_file="F" * 64))
    assert "car.rule_file" in refusal(with_car(platform="TOYOTA_COROLLA", rule_file="f" * 63))


# --- periods -------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["periods", "log_periods"])
def test_period_floor_and_ceiling(field):
    for value in (10, 20, 60_000):
        car = parse_profile({**base(), "car": {"platform": "TOYOTA_COROLLA", field: {"WHEEL_SPEEDS": value}}}).car
        assert getattr(car, field) == {"WHEEL_SPEEDS": value}
    for value in (9, 0, -20, 60_001, 10**9):
        message = refusal({**base(), "car": {"platform": "TOYOTA_COROLLA", field: {"WHEEL_SPEEDS": value}}})
        assert f"car.{field}.WHEEL_SPEEDS" in message


@pytest.mark.parametrize("field", ["periods", "log_periods"])
@pytest.mark.parametrize("value", [True, False, 20.0, 20.5, "20", None, [20], {"ms": 20}, float("nan"), float("inf")])
def test_periods_reject_bool_float_and_out_of_range(field, value):
    message = refusal({**base(), "car": {"platform": "TOYOTA_COROLLA", field: {"WHEEL_SPEEDS": value}}})
    assert f"car.{field}.WHEEL_SPEEDS" in message


@pytest.mark.parametrize("field", ["periods", "log_periods"])
def test_period_name_rules(field):
    def car(name):
        return {**base(), "car": {"platform": "TOYOTA_COROLLA", field: {name: 20}}}

    assert getattr(parse_profile(car("A")).car, field) == {"A": 20}
    assert getattr(parse_profile(car("N" * 128)).car, field) == {"N" * 128: 20}
    assert getattr(parse_profile(car("WHEEL SPEEDS é")).car, field) == {"WHEEL SPEEDS é": 20}

    for name in ("", "N" * 129, "A\nB", "A\x00B", "A\tB", "\x7f", "A\x85B", "\r"):
        assert f"car.{field}" in refusal(car(name)), repr(name)


@pytest.mark.parametrize("field", ["periods", "log_periods"])
def test_period_maps_are_bounded(field):
    def car(count):
        return {**base(), "car": {"platform": "TOYOTA_COROLLA", field: {f"M{index}": 20 for index in range(count)}}}

    assert len(getattr(parse_profile(car(5000)).car, field)) == 5000
    assert f"car.{field}" in refusal(car(5001))


def test_periods_and_log_periods_are_independent_and_default_empty():
    car = parse_profile(with_car(platform="TOYOTA_COROLLA", periods={"A": 20}, log_periods={"A": 50, "B": 100})).car
    assert (car.periods, car.log_periods) == ({"A": 20}, {"A": 50, "B": 100})
    bare = parse_profile(with_car(platform="TOYOTA_COROLLA")).car
    assert (bare.periods, bare.log_periods) == ({}, {})


# --- against the other bus sources ---------------------------------------------------------------


def test_car_excludes_restbus_and_dbc():
    restbus = load_yaml(PROFILES / "wheel_speed_demo.yaml")
    restbus["car"] = {"platform": "TOYOTA_COROLLA"}
    assert "choose either restbus, dbc or car" in refusal(restbus)

    with_dbc = base()
    with_dbc["dbc"] = {"filename": "example.dbc", "sha256": UPLOAD_SHA, "messages": []}
    with_dbc["car"] = {"platform": "TOYOTA_COROLLA"}
    assert "choose either restbus, dbc or car" in refusal(with_dbc)


def test_car_with_restbus_or_dbc_is_refused_both_ways():
    restbus = load_yaml(PROFILES / "wheel_speed_demo.yaml")["restbus"]
    dbc = {"filename": "example.dbc", "sha256": UPLOAD_SHA, "messages": []}
    car = {"platform": "TOYOTA_COROLLA"}
    sections = {"restbus": restbus, "dbc": dbc, "car": car}

    # Each section alone is a valid profile: the refusals below are about the combination.
    for name, section in sections.items():
        parsed = parse_profile({**base(), name: section})
        assert [key for key in sections if getattr(parsed, key) is not None] == [name]

    for names in (("restbus", "dbc"), ("restbus", "car"), ("dbc", "car"), ("restbus", "dbc", "car"), ("car", "dbc")):
        data = {**base(), **{name: sections[name] for name in names}}
        assert "choose either restbus, dbc or car" in refusal(data), names


def test_the_combination_is_the_only_problem_reported():
    data = {**base(), "restbus": load_yaml(PROFILES / "wheel_speed_demo.yaml")["restbus"]}
    data["dbc"] = {"filename": "example.dbc", "sha256": UPLOAD_SHA, "messages": []}
    data["car"] = {"platform": "TOYOTA_COROLLA"}
    message = refusal(data)
    assert "1 problem(s)" in message and message.count("choose either restbus, dbc or car") == 1


# --- the shipped profiles ------------------------------------------------------------------------


def test_existing_profiles_still_parse():
    paths = sorted(PROFILES.glob("*.yaml"))
    assert len(paths) >= 8
    for path in paths:
        profile = load_profile(path)
        if path == DEMO:
            assert profile.car is not None
        else:
            assert profile.car is None, path.name


def test_demo_profile_parses_and_names_corolla():
    profile = load_profile(DEMO)
    assert profile.car is not None
    assert (profile.car.platform, profile.car.bus) == ("TOYOTA_COROLLA", "pt")
    assert (profile.car.filename, profile.car.sha256, profile.car.rule, profile.car.rule_file) == (None,) * 4
    assert (profile.car.periods, profile.car.log_periods) == ({}, {})
    assert profile.restbus is None and profile.dbc is None
    assert "TOYOTA_COROLLA" in load_catalog().platforms

    driven = {signal.path: signal for signal in profile.scenario.signals}
    assert list(driven) == ["vehicle.speed", "engine.rpm"]
    assert driven["vehicle.speed"].type == "stepped" and driven["engine.rpm"].type == "stepped"
    assert [driven[path].interval for path in driven] == [10, 10]
    assert list(driven["vehicle.speed"].values) == [0, 40, 0]
    assert list(driven["engine.rpm"].values) == [800, 2000, 800]

    # A VIN shorter than 17 characters is left-padded with NUL bytes in the mode 09 reply, so
    # every shipped profile must carry a full one, and the demo is one of them.
    assert len(profile.vehicle.vin) == VIN_LENGTH
    for path in sorted(PROFILES.glob("*.yaml")):
        vin = load_profile(path).vehicle.vin
        assert len(vin) == VIN_LENGTH, f"{path.name}: VIN {vin!r} has {len(vin)} characters, not {VIN_LENGTH}"
        assert mode09_vin_reply(load_profile(path)) == b"\x49\x02\x01" + vin.encode(), path.name


def test_demo_profile_is_listed_and_packaged_like_the_others(tmp_path):
    assert "car_toyota_demo" in Profiles(tmp_path).names()["shipped"]

    root = Path(__file__).resolve().parents[2]
    patterns = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"][
        "targets"
    ]["wheel"]["artifacts"]
    for path in PROFILES.glob("*.yaml"):
        relative = path.relative_to(root).as_posix()
        assert any(fnmatch.fnmatch(relative, pattern) for pattern in patterns), relative


# --- the schema stays a leaf ---------------------------------------------------------------------

SCHEMA_IMPORTS = """
import json
import sys

import ecu_simulator.config.schema

RUN_MODULES = (
    "ecu_simulator.app",
    "ecu_simulator.dbc",
    "ecu_simulator.car.integrity",
    "ecu_simulator.car.drive",
    "ecu_simulator.car.rules",
    "ecu_simulator.car.busload",
    "ecu_simulator.car.periods",
)


def leaked():
    return sorted(
        name
        for name in sys.modules
        if name in RUN_MODULES or (name.startswith("ecu_simulator.car.") and name != "ecu_simulator.car.catalog")
    )


after_import = leaked()
from ecu_simulator.config.schema import CarConfig

CarConfig(platform="TOYOTA_COROLLA")
print(json.dumps({
    "after_import": after_import,
    "after_validation": leaked(),
    "catalog_loaded": "ecu_simulator.car.catalog" in sys.modules,
}))
"""


def test_importing_the_schema_does_not_import_the_car_run_modules():
    result = subprocess.run(
        [sys.executable, "-I", "-c", SCHEMA_IMPORTS], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["after_import"] == []
    assert seen["after_validation"] == []
    assert seen["catalog_loaded"] is True
