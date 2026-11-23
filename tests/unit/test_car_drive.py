"""The generated drive: which signals of a DBC message follow the simulated vehicle or a time pattern.

Unit tests build messages the way ``ImportedDb.message`` returns them and drive a real ``VehicleState``, so a missing
``engine.rpm`` is detected the way the running simulator detects it. One test runs the classifier over the shipped
powertrain DBCs of three brands and encodes what the sampler returns.
"""

import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ecu_simulator.car.catalog import load_catalog
from ecu_simulator.car.drive import DriveRole, classify_signal, drive_sampler
from ecu_simulator.dbc import DbcStore
from ecu_simulator.vehicle import BevPowertrain, CommonState, HevPowertrain, IcePowertrain, VehicleState


def signal(
    name: str, unit: str = "", low: float | None = None, high: float | None = None, *, is_mux: bool = False
) -> dict[str, Any]:
    """The part of ``ImportedDb``'s signal dict the drive reads."""
    return {"name": name, "unit": unit, "min": low, "max": high, "default": 0.0, "is_mux": is_mux}


def message(*signals: dict[str, Any]) -> dict[str, Any]:
    return {"name": "TEST_MESSAGE", "signals": list(signals)}


def ice(speed: int = 0, rpm: int = 0) -> VehicleState:
    vehicle = VehicleState(CommonState(vin="TESTVIN0123456789"), IcePowertrain())
    vehicle.set("vehicle.speed", speed)
    vehicle.set("engine.rpm", rpm)
    return vehicle


def bev(speed: int = 0) -> VehicleState:
    vehicle = VehicleState(CommonState(vin="TESTVIN0123456789"), BevPowertrain())
    vehicle.set("vehicle.speed", speed)
    return vehicle


class Clock:
    """An ``elapsed`` callable the test moves by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("name", "unit", "expected"),
    [
        # speed: km/h and kph by unit alone, whatever the signal is called
        ("WHEEL_SPEED_FL", "km/h", DriveRole.SPEED),
        ("WHEEL_SPEED_FL", "kph", DriveRole.SPEED),
        ("SPEED", "KPH", DriveRole.SPEED),
        ("SPEED", "Km/H", DriveRole.SPEED),
        ("SPEED", " km/h ", DriveRole.SPEED),
        ("ANYTHING", "kph", DriveRole.SPEED),
        # m/s and mph only with SPEED in the name
        ("VEHICLE_SPEED", "m/s", DriveRole.SPEED),
        ("vehicle_speed", "M/S", DriveRole.SPEED),
        ("SPEED_MPH", "mph", DriveRole.SPEED),
        ("Speed", "MPH", DriveRole.SPEED),
        ("LATERAL_ACCEL", "m/s", None),
        ("ODOMETER", "mph", None),
        # units that only look like a speed unit
        ("VEHICLE_SPEED", "km/h or MPH", None),
        ("VEHICLE_SPEED", "km/h / mph", None),
        ("VEHICLE_SPEED", "m/s^2", None),
        ("VEHICLE_SPEED", "m/s2", None),
        ("VEHICLE_SPEED", "", None),
        ("VEHICLE_SPEED", "km", None),
        # rpm
        ("ENGINE_RPM", "rpm", DriveRole.RPM),
        ("RPM", "RPM", DriveRole.RPM),
        ("ENGINE_RPM", "rad/s", None),
        # steering: deg and a STEER ... ANGLE name
        ("STEER_ANGLE", "deg", DriveRole.STEERING),
        ("STEERING_ANGLE", "Deg", DriveRole.STEERING),
        ("steering_wheel_angle", "deg", DriveRole.STEERING),
        ("STEER_ANGLE_RATE", "deg/s", None),
        ("STEER_TORQUE", "deg", None),
        ("ANGLE_STEER", "deg", None),
        ("STEER_ANGLE", "rad", None),
        ("STEER_ANGLE", "", None),
        ("ROAD_WHEEL_ANGLE", "deg", None),
        # pedal: % and PEDAL in the name, never a brake
        ("GAS_PEDAL", "%", DriveRole.PEDAL),
        ("accel_pedal_pos", "%", DriveRole.PEDAL),
        ("ACCELERATOR_PEDAL", " % ", DriveRole.PEDAL),
        ("BRAKE_PEDAL", "%", None),
        ("brake_pedal_position", "%", None),
        ("PEDAL_BRAKE", "%", None),
        ("GAS_PEDAL", "V", None),
        ("GAS_PEDAL", "", None),
        ("FUEL_LEVEL", "%", None),
        ("THROTTLE_POSITION", "%", None),
        # nothing else: gear, brake, counters, flags
        ("GEAR", "", None),
        ("BRAKE_PRESSURE", "", None),
        ("COUNTER", "", None),
        ("CHECKSUM", "", None),
        ("TIRE_PRESSURE", "kPa", None),
    ],
)
def test_classify_speed_rpm_steering_pedal_and_nothing_else(name: str, unit: str, expected: DriveRole | None) -> None:
    assert classify_signal(signal(name, unit)) == expected


def test_a_multiplexer_selector_is_never_driven() -> None:
    assert classify_signal(signal("SPEED_SELECTOR", "km/h", is_mux=True)) is None
    assert classify_signal(signal("SPEED_SELECTOR", "km/h", is_mux=False)) is DriveRole.SPEED


def test_the_roles_are_named_speed_rpm_steering_pedal() -> None:
    assert [role.value for role in DriveRole] == ["speed", "rpm", "steering", "pedal"]


def test_wheel_speeds_follow_vehicle_speed() -> None:
    vehicle = ice(speed=0)
    sample = drive_sampler(
        message(
            signal("WHEEL_SPEED_FL", "km/h", 0, 250),
            signal("WHEEL_SPEED_FR", "km/h", 0, 250),
            signal("COUNTER", ""),
        ),
        vehicle,
        Clock(),
    )

    assert sample() == {"WHEEL_SPEED_FL": 0.0, "WHEEL_SPEED_FR": 0.0}
    vehicle.set("vehicle.speed", 80)
    assert sample() == {"WHEEL_SPEED_FL": 80.0, "WHEEL_SPEED_FR": 80.0}
    vehicle.set("vehicle.speed", 33)
    assert sample() == {"WHEEL_SPEED_FL": 33.0, "WHEEL_SPEED_FR": 33.0}


def test_speed_is_a_float_even_though_the_vehicle_holds_an_integer() -> None:
    value = drive_sampler(message(signal("SPEED", "kph")), ice(speed=80), Clock())()["SPEED"]
    assert type(value) is float


def test_m_per_s_and_mph_conversions() -> None:
    vehicle = ice(speed=72)
    sample = drive_sampler(
        message(signal("SPEED_MS", "m/s"), signal("SPEED_MPH", "mph"), signal("SPEED_KMH", "km/h")), vehicle, Clock()
    )

    values = sample()
    assert values["SPEED_KMH"] == 72.0
    assert values["SPEED_MS"] == pytest.approx(20.0)
    assert values["SPEED_MPH"] == pytest.approx(72 / 1.609344)
    assert values["SPEED_MPH"] == pytest.approx(44.7388, abs=1e-4)


def test_speed_is_clamped_to_the_signals_range() -> None:
    vehicle = ice(speed=300)
    sample = drive_sampler(
        message(signal("CAPPED", "km/h", 0, 260), signal("OPEN_ENDED", "km/h"), signal("FLOORED", "km/h", 350, None)),
        vehicle,
        Clock(),
    )

    assert sample() == {"CAPPED": 260.0, "OPEN_ENDED": 300.0, "FLOORED": 350.0}
    vehicle.set("vehicle.speed", -5)
    assert sample()["CAPPED"] == 0.0


def test_rpm_follows_engine_rpm_only_when_vehicle_has_it() -> None:
    drive = message(signal("ENGINE_RPM", "rpm", 0, 8000), signal("SPEED", "km/h"))

    ice_vehicle = ice(speed=10, rpm=3000)
    sample = drive_sampler(drive, ice_vehicle, Clock())
    assert sample()["ENGINE_RPM"] == 3000.0
    ice_vehicle.set("engine.rpm", 9000)
    assert sample()["ENGINE_RPM"] == 8000.0

    hybrid = VehicleState(CommonState(vin="TESTVIN0123456789"), HevPowertrain())
    hybrid.set("engine.rpm", 1500)
    assert drive_sampler(drive, hybrid, Clock())()["ENGINE_RPM"] == 1500.0

    electric = bev(speed=10)
    assert not electric.has("engine.rpm")
    assert drive_sampler(drive, electric, Clock())() == {"SPEED": 10.0}


def test_steering_is_repeatable_and_clamped() -> None:
    clock = Clock(5.0)  # a quarter of the 20 s period: the sine is at its peak
    free = drive_sampler(message(signal("STEER_ANGLE", "deg", -3000, 3000)), ice(), clock)
    tight = drive_sampler(message(signal("STEER_ANGLE", "deg", -10, 10)), ice(), clock)

    assert free()["STEER_ANGLE"] == pytest.approx(30.0)
    assert free()["STEER_ANGLE"] == free()["STEER_ANGLE"]
    assert tight()["STEER_ANGLE"] == 10.0
    clock.now = 15.0
    assert free()["STEER_ANGLE"] == pytest.approx(-30.0)
    assert tight()["STEER_ANGLE"] == -10.0
    for now in (0.0, 20.0, 40.0):
        clock.now = now
        assert free()["STEER_ANGLE"] == pytest.approx(0.0, abs=1e-9)


def test_steering_and_pedal_do_not_depend_on_the_wall_clock_or_the_vehicle() -> None:
    drive = message(signal("STEER_ANGLE", "deg", -90, 90), signal("GAS_PEDAL", "%", 0, 100))
    clock = Clock(7.5)
    first = drive_sampler(drive, ice(speed=0), clock)
    second = drive_sampler(drive, ice(speed=200, rpm=6000), clock)

    assert first() == second()
    assert first() == first()


def test_pedal_steps_zero_twelve_zero_every_ten_seconds() -> None:
    clock = Clock()
    sample = drive_sampler(message(signal("GAS_PEDAL", "%", 0, 100)), ice(), clock)

    seen = []
    for now in (0.0, 9.99, 10.0, 19.99, 20.0, 29.99, 30.0, 40.0, 60.0):
        clock.now = now
        seen.append(sample()["GAS_PEDAL"])
    assert seen == [0.0, 0.0, 12.0, 12.0, 0.0, 0.0, 0.0, 12.0, 0.0]


def test_pedal_is_clamped_to_the_signals_range() -> None:
    clock = Clock(10.0)
    sample = drive_sampler(
        message(signal("GAS_PEDAL", "%", 0, 5), signal("GAS_PEDAL_OFFSET", "%", 20, 100)), ice(), clock
    )

    assert sample() == {"GAS_PEDAL": 5.0, "GAS_PEDAL_OFFSET": 20.0}


def test_unrecognised_signals_are_absent_from_the_sample() -> None:
    sample = drive_sampler(
        message(
            signal("GEAR", ""),
            signal("BRAKE_PEDAL", "%", 0, 100),
            signal("BRAKE_PRESSURE", "kPa"),
            signal("COUNTER", ""),
            signal("CHECKSUM", ""),
            signal("LATERAL_ACCEL", "m/s"),
            signal("SELECTOR", "km/h", is_mux=True),
            signal("SPEED", "km/h"),
        ),
        ice(speed=50),
        Clock(12.0),
    )

    assert sample() == {"SPEED": 50.0}


def test_a_message_with_nothing_recognised_samples_empty() -> None:
    sample = drive_sampler(message(signal("GEAR", ""), signal("CHECKSUM", "")), ice(speed=50), Clock())

    assert sample() == {}


def test_a_non_finite_value_is_left_out_so_the_encoder_keeps_the_default() -> None:
    vehicle = ice(speed=50, rpm=2000)
    sample = drive_sampler(
        message(signal("SPEED", "km/h", 0, 250), signal("ENGINE_RPM", "rpm", 0, 8000)), vehicle, Clock()
    )

    vehicle.set("vehicle.speed", math.nan)
    vehicle.set("engine.rpm", math.nan)
    assert sample() == {}
    vehicle.set("vehicle.speed", 20)
    assert sample() == {"SPEED": 20.0}


def test_every_produced_value_is_finite() -> None:
    clock = Clock()
    sample = drive_sampler(
        message(signal("STEER_ANGLE", "deg", -540, 540), signal("GAS_PEDAL", "%", 0, 100), signal("SPEED", "m/s")),
        ice(speed=123),
        clock,
    )
    for now in range(0, 100):
        clock.now = now / 3
        assert all(math.isfinite(value) for value in sample().values())


def test_importing_the_drive_does_not_create_an_import_cycle() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import ecu_simulator.car.drive"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


# Platforms picked from the catalog: one per brand, each with a powertrain DBC the catalog ships.
REAL_PLATFORMS = ["TOYOTA_COROLLA", "HONDA_CIVIC_BOSCH", "HYUNDAI_ELANTRA"]


@pytest.mark.parametrize("platform_id", REAL_PLATFORMS)
def test_the_shipped_powertrain_dbcs_have_speed_signals_and_no_brake_pedal(platform_id: str, tmp_path: Path) -> None:
    catalog = load_catalog()
    entry = catalog.dbcs[catalog.platforms[platform_id].buses["pt"]]
    db = DbcStore(tmp_path).load(entry.sha256)
    messages = db.catalog(entry.name)["messages"]

    roles: dict[DriveRole, list[str]] = {role: [] for role in DriveRole}
    for info in messages:
        for sig in info["signals"]:
            role = classify_signal(sig)
            if role is not None:
                roles[role].append(f"{info['name']}.{sig['name']}")
            if "BRAKE" in sig["name"].upper():
                assert role is not DriveRole.PEDAL, (info["name"], sig["name"])

    assert roles[DriveRole.SPEED], f"{platform_id}: no signal followed the vehicle speed"


@pytest.mark.parametrize("platform_id", REAL_PLATFORMS)
def test_the_sampler_output_encodes_for_every_message_of_the_shipped_powertrain_dbcs(
    platform_id: str, tmp_path: Path
) -> None:
    catalog = load_catalog()
    entry = catalog.dbcs[catalog.platforms[platform_id].buses["pt"]]
    db = DbcStore(tmp_path).load(entry.sha256)
    vehicle = ice(speed=88, rpm=2400)
    clock = Clock(12.5)

    driven = 0
    for info in db.catalog(entry.name)["messages"]:
        if info["layout_issue"]:
            continue
        values = drive_sampler(db.message(info["name"]), vehicle, clock)()
        assert values.keys() <= {sig["name"] for sig in info["signals"]}
        db.encode(info["name"], values, integrity_ok=True)
        driven += bool(values)
    assert driven, platform_id
