"""Composed vehicle state addressed by dotted signal paths."""

import pytest

from ecu_simulator.vehicle import (
    BevPowertrain,
    Charging,
    CommonState,
    EMotor,
    HevPowertrain,
    IcePowertrain,
    IceState,
    TractionBattery,
    UnknownSignalError,
    VehicleState,
)


def ice_vehicle(**engine):
    engine = {"fuel_level": 50, "fuel_type": 1, **engine}
    return VehicleState(CommonState(vin="TESTVIN0123456789"), IcePowertrain(engine=IceState(**engine)))


def bev_vehicle():
    return VehicleState(
        CommonState(vin="BEVVIN00000000001"),
        BevPowertrain(battery=TractionBattery(soc=80.0), motor=EMotor(), charging=Charging()),
    )


# --- composition ------------------------------------------------------------------------------


def test_an_ice_vehicle_has_an_engine_and_no_traction_battery():
    vehicle = ice_vehicle()
    assert vehicle.powertrain.kind == "ice"
    assert "engine.rpm" in vehicle.signals
    assert "battery.soc" not in vehicle.signals


def test_a_bev_vehicle_has_a_battery_motor_and_charging_but_no_engine():
    vehicle = bev_vehicle()
    assert vehicle.powertrain.kind == "bev"
    assert {"battery.soc", "motor.rpm", "charging.power"} <= set(vehicle.signals)
    assert not any(path.startswith("engine.") for path in vehicle.signals)


def test_a_hev_vehicle_has_both():
    vehicle = VehicleState(
        CommonState(vin="HEVVIN00000000001"),
        HevPowertrain(engine=IceState(), battery=TractionBattery(), motor=EMotor(), charging=Charging()),
    )
    assert vehicle.powertrain.kind == "hev"
    assert {"engine.rpm", "battery.soc", "motor.rpm"} <= set(vehicle.signals)


def test_common_signals_exist_on_every_powertrain():
    for vehicle in (ice_vehicle(), bev_vehicle()):
        assert {"vehicle.vin", "vehicle.speed", "vehicle.ambient_temp"} <= set(vehicle.signals)


# --- reading and writing signals ----------------------------------------------------------------


def test_read_a_signal_by_dotted_path():
    vehicle = ice_vehicle()
    assert vehicle.get("vehicle.vin") == "TESTVIN0123456789"
    assert vehicle.get("engine.fuel_level") == 50
    assert vehicle.get("engine.fuel_type") == 1


def test_reading_an_unknown_signal_names_the_path_and_does_not_return_none():
    vehicle = ice_vehicle()
    with pytest.raises(UnknownSignalError, match="battery.soc"):
        vehicle.get("battery.soc")
    with pytest.raises(UnknownSignalError, match="engine.nonesuch"):
        vehicle.get("engine.nonesuch")
    with pytest.raises(UnknownSignalError, match="rpm"):
        vehicle.get("rpm")


def test_has_reports_whether_a_signal_exists():
    vehicle = ice_vehicle()
    assert vehicle.has("engine.rpm") is True
    assert vehicle.has("battery.soc") is False


def test_set_updates_a_signal_in_place():
    vehicle = ice_vehicle()
    vehicle.set("engine.rpm", 2500)
    assert vehicle.get("engine.rpm") == 2500
    assert vehicle.powertrain.engine.rpm == 2500


def test_setting_an_unknown_signal_is_rejected():
    vehicle = ice_vehicle()
    with pytest.raises(UnknownSignalError, match="battery.soc"):
        vehicle.set("battery.soc", 50.0)


def test_reads_have_no_side_effects():
    # Unlike the legacy speed counter (DEV-09), reading state never changes it.
    vehicle = ice_vehicle()
    first = [vehicle.get(path) for path in sorted(vehicle.signals)]
    second = [vehicle.get(path) for path in sorted(vehicle.signals)]
    assert first == second


def test_signals_maps_every_path_to_its_current_value():
    vehicle = ice_vehicle()
    assert vehicle.signals["engine.fuel_level"] == 50
    vehicle.set("engine.fuel_level", 20)
    assert vehicle.signals["engine.fuel_level"] == 20


# --- defaults and ranges -------------------------------------------------------------------------


def test_ice_state_defaults_are_deterministic_and_inert():
    engine = IceState()
    assert (engine.rpm, engine.coolant_temp, engine.fuel_level) == (0, 20.0, 50)


def test_common_state_defaults_are_deterministic():
    common = CommonState(vin="TESTVIN0123456789")
    assert (common.speed, common.ambient_temp, common.odometer) == (0, 20.0, 0)


def test_namespaces_are_stable_and_unique():
    vehicle = VehicleState(
        CommonState(vin="HEVVIN00000000001"),
        HevPowertrain(engine=IceState(), battery=TractionBattery(), motor=EMotor(), charging=Charging()),
    )
    namespaces = {path.split(".")[0] for path in vehicle.signals}
    assert namespaces == {"vehicle", "engine", "battery", "motor", "charging"}
