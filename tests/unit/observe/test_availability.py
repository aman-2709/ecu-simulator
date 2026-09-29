"""The ``unavailable`` list (0010 §5, ninth revision): signal paths with no source in the
loaded profile, neither settable by the profile schema nor driven by its scenario.
"""

from pathlib import Path

import pytest

from ecu_simulator import app
from ecu_simulator.config import load_profile, load_yaml, parse_profile
from ecu_simulator.observe import availability
from ecu_simulator.vehicle import signal_types

PROFILES = Path(app.__file__).parent / "profiles"
EXAMPLES = Path(app.__file__).parents[2] / "docs" / "examples"
SHIPPED = [PROFILES / "ice_default.yaml", PROFILES / "ice_scenario.yaml", EXAMPLES / "ice_drive_cycle_stepped.yaml"]


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.name)
def test_the_shipped_profiles_and_the_stepped_demo_lack_only_the_odometer(path):
    assert availability.unavailable(load_profile(path)) == ("vehicle.odometer",)


def test_a_scenario_that_drives_the_odometer_removes_it():
    raw = load_yaml(PROFILES / "ice_default.yaml")
    raw["scenario"] = {"signals": [{"path": "vehicle.odometer", "type": "constant", "value": 12000}]}
    assert availability.unavailable(parse_profile(raw)) == ()


def test_every_other_ice_signal_is_settable_by_the_schema():
    # VIN, obd_standard, fuel_type and the rest are profile fields, so they are never listed,
    # whether or not a given profile writes them (the defaults are the schema's).
    assert availability.settable(signal_types("ice")) == frozenset(signal_types("ice")) - {"vehicle.odometer"}


def test_hev_and_bev_list_their_unconfigurable_components():
    raw = load_yaml(PROFILES / "ice_default.yaml")
    raw["vehicle"] = {"vin": "TESTVIN0123456789", "type": "bev", "battery": {"soc": 80}}
    assert availability.unavailable(parse_profile(raw)) == (
        "battery.current", "charging.active", "charging.power", "motor.rpm", "motor.temp", "motor.torque",
        "vehicle.odometer",
    )
    raw["vehicle"] = {"vin": "TESTVIN0123456789", "type": "hev"}
    raw["scenario"] = {"signals": [{"path": "motor.rpm", "type": "constant", "value": 1000}]}
    assert availability.unavailable(parse_profile(raw)) == (
        "battery.current", "charging.active", "charging.power", "motor.temp", "motor.torque", "vehicle.odometer",
    )


def test_the_list_is_sorted_and_immutable():
    raw = load_yaml(PROFILES / "ice_default.yaml")
    raw["vehicle"] = {"vin": "TESTVIN0123456789", "type": "bev"}
    result = availability.unavailable(parse_profile(raw))
    assert isinstance(result, tuple) and list(result) == sorted(result) and len(result) > 1
