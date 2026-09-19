"""Parameter definitions: physical value in, wire bytes out, support derived from signals."""

import pytest

from ecu_simulator.protocols.obd import masks
from ecu_simulator.protocols.obd.pids import (
    DEFERRED_MODE01_PIDS,
    MODE01_DEFINITIONS,
    MODE01_PIDS,
    supported_pids,
)
from ecu_simulator.vehicle import (
    BevPowertrain,
    Charging,
    CommonState,
    EMotor,
    IcePowertrain,
    IceState,
    TractionBattery,
    VehicleState,
)


def ice(**engine):
    return VehicleState(CommonState(vin="TESTVIN0123456789"), IcePowertrain(engine=IceState(**engine)))


def bev():
    return VehicleState(
        CommonState(vin="BEVVIN00000000001"),
        BevPowertrain(battery=TractionBattery(), motor=EMotor(), charging=Charging()),
    )


def encode(pid, vehicle):
    return MODE01_PIDS[pid].read(vehicle)


# --- table integrity -------------------------------------------------------------------------


def test_every_definition_is_self_consistent():
    for definition in MODE01_DEFINITIONS:
        assert 0 <= definition.pid <= 0xFF
        assert definition.length in (1, 2)
        assert definition.signals, definition.pid
        assert definition.evidence, definition.pid


def test_no_definition_collides_with_a_range_identifier():
    # 0x00, 0x20, 0x40 ... are the supported-parameter identifiers, not data parameters.
    assert not any(masks.is_range_request(d.pid) for d in MODE01_DEFINITIONS)


def test_identifiers_are_unique():
    pids = [d.pid for d in MODE01_DEFINITIONS]
    assert len(set(pids)) == len(pids)


def test_deferred_identifiers_are_recorded_and_absent():
    for pid, reason in DEFERRED_MODE01_PIDS.items():
        assert pid not in MODE01_PIDS
        assert reason


# --- support follows the configured vehicle -----------------------------------------------------


def test_an_ice_vehicle_supports_the_engine_parameters():
    assert supported_pids(ice()) == frozenset(MODE01_PIDS)


def test_a_battery_electric_vehicle_supports_no_engine_parameters():
    supported = supported_pids(bev())
    assert all(not MODE01_PIDS[pid].signals[0].startswith("engine.") for pid in supported)
    assert supported, "common parameters such as vehicle speed remain supported"


def test_support_is_computed_not_declared():
    # Removing the signal removes the parameter, with nothing listing it by hand.
    engine_pids = {pid for pid, d in MODE01_PIDS.items() if any(s.startswith("engine.") for s in d.signals)}
    assert engine_pids <= supported_pids(ice())
    assert engine_pids.isdisjoint(supported_pids(bev()))


# --- encodings -------------------------------------------------------------------------------


def test_coolant_temperature_is_offset_by_forty():
    assert encode(0x05, ice(coolant_temp=90.0)) == b"\x82"
    assert encode(0x05, ice(coolant_temp=-40.0)) == b"\x00"
    assert encode(0x05, ice(coolant_temp=0.0)) == b"\x28"


def test_percentages_truncate_rather_than_round():
    # 50 per cent is 127.5 counts; this project has always emitted 0x7F, not 0x80.
    assert encode(0x2F, ice(fuel_level=50)) == b"\x7f"
    assert encode(0x2F, ice(fuel_level=0)) == b"\x00"
    assert encode(0x2F, ice(fuel_level=100)) == b"\xff"


def test_values_are_clamped_to_the_field_they_must_fit():
    assert encode(0x0D, VehicleState(CommonState(vin="X", speed=300), IcePowertrain())) == b"\xff"
    assert encode(0x05, ice(coolant_temp=-100.0)) == b"\x00"


def test_reading_a_parameter_never_changes_the_vehicle():
    vehicle = ice(fuel_level=50, coolant_temp=90.0)
    before = dict(vehicle.signals)
    for pid in supported_pids(vehicle):
        MODE01_PIDS[pid].read(vehicle)
        MODE01_PIDS[pid].read(vehicle)
    assert dict(vehicle.signals) == before


def test_an_encoder_that_produces_the_wrong_length_is_rejected():
    definition = MODE01_PIDS[0x05]
    broken = type(definition)(
        pid=definition.pid,
        name=definition.name,
        unit=definition.unit,
        signals=definition.signals,
        encode=lambda v: b"\x00\x00\x00",
        length=1,
        evidence="test",
    )
    with pytest.raises(ValueError, match="expected 1"):
        broken.read(ice())


# --- masks ------------------------------------------------------------------------------------


def test_mask_sets_the_bit_for_each_supported_identifier():
    assert masks.supported_mask(0x00, {0x05, 0x0D, 0x2F}).hex() == "08080001"
    assert masks.supported_mask(0x20, {0x2F, 0x51}).hex() == "00020001"
    assert masks.supported_mask(0x40, {0x51}).hex() == "00008000"


def test_mask_ignores_identifiers_outside_its_range():
    assert masks.supported_mask(0x00, {0x2F, 0x51}).hex() == "00000001"


def test_the_last_range_never_claims_a_successor():
    assert masks.supported_mask(0xE0, set()).hex() == "00000000"


# --- DEV-04: the continuation bit follows the populated ranges ---------------------------------


def test_no_identifier_above_the_first_range_clears_the_continuation_bit():
    assert masks.supported_mask(0x00, {0x05, 0x0D}).hex() == "08080000"


def test_the_range_identifier_is_never_set_as_a_data_bit():
    # Bit 0 of a mask is the next range identifier, set only by the continuation rule.
    # No data parameter may occupy it, which test_no_definition_collides_with_a_range_
    # identifier enforces for the real table.
    assert masks.supported_mask(0x00, {0x20}).hex() == "00000000"
    assert masks.supported_mask(0x20, {0x40}).hex() == "00000000"


def test_an_identifier_in_the_second_range_sets_the_bit_in_the_first_mask():
    assert masks.supported_mask(0x00, {0x05, 0x2F}).hex() == "08000001"
    assert masks.supported_mask(0x00, {0x05, 0x40}).hex() == "08000001"


def test_no_identifier_above_the_second_range_clears_its_continuation_bit():
    assert masks.supported_mask(0x20, {0x2F}).hex() == "00020000"
    assert masks.supported_mask(0x20, {0x2F, 0x51}).hex() == "00020001"


def test_only_ranges_the_chain_reaches_are_advertised():
    supported = {0x05, 0x2F, 0x51}
    assert masks.is_advertised_range(0x00, supported) is True
    assert masks.is_advertised_range(0x20, supported) is True
    assert masks.is_advertised_range(0x40, supported) is True
    assert masks.is_advertised_range(0x60, supported) is False
    assert masks.is_advertised_range(0xE0, supported) is False


def test_a_vehicle_with_only_first_range_parameters_advertises_only_the_first_range():
    supported = {0x05, 0x0D}
    assert masks.is_advertised_range(0x00, supported) is True
    assert masks.is_advertised_range(0x20, supported) is False


def test_a_non_range_identifier_is_never_an_advertised_range():
    assert masks.is_advertised_range(0x05, {0x05}) is False
    assert masks.is_advertised_range(0x100, {0x05}) is False


def test_adding_and_removing_a_parameter_changes_the_mask_deterministically():
    base = {0x05, 0x0D}
    assert masks.supported_mask(0x00, base).hex() == "08080000"
    with_next_range = base | {0x2F}
    assert masks.supported_mask(0x00, with_next_range).hex() == "08080001"
    assert masks.supported_mask(0x00, with_next_range - {0x2F}).hex() == "08080000"
    assert masks.supported_mask(0x00, base) == masks.supported_mask(0x00, set(base))


def test_range_requests_are_recognised():
    assert [p for p in range(0x100) if masks.is_range_request(p)] == list(range(0x00, 0x100, 0x20))
    assert masks.range_bases() == (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0)


# --- boundary values, one case set per parameter ---------------------------------------------
#
# Each row is (signal, physical value, expected bytes). Evidence for each formula is
# recorded on its definition in pids.py; none is standards validated.

BOUNDARIES = [
    # Calculated engine load, A * 100 / 255
    (0x04, "engine.engine_load", 0.0, "00"),
    (0x04, "engine.engine_load", 22.0, "38"),
    (0x04, "engine.engine_load", 100.0, "ff"),
    # Engine coolant temperature, A - 40
    (0x05, "engine.coolant_temp", -40.0, "00"),
    (0x05, "engine.coolant_temp", 90.0, "82"),
    (0x05, "engine.coolant_temp", 215.0, "ff"),
    # Fuel trims, (A - 128) * 100 / 128
    (0x06, "engine.short_fuel_trim", -100.0, "00"),
    (0x06, "engine.short_fuel_trim", 0.0, "80"),
    (0x07, "engine.long_fuel_trim", 99.2, "fe"),
    (0x07, "engine.long_fuel_trim", 99.21875, "ff"),  # the largest representable trim
    # Intake manifold absolute pressure, A kPa
    (0x0B, "engine.map", 0, "00"),
    (0x0B, "engine.map", 33, "21"),
    (0x0B, "engine.map", 255, "ff"),
    # Engine speed, (256A + B) / 4
    (0x0C, "engine.rpm", 0, "0000"),
    (0x0C, "engine.rpm", 800, "0c80"),
    (0x0C, "engine.rpm", 16383, "fffc"),
    # Vehicle speed, A km/h
    (0x0D, "vehicle.speed", 0, "00"),
    (0x0D, "vehicle.speed", 255, "ff"),
    # Timing advance, A / 2 - 64
    (0x0E, "engine.timing_advance", -64.0, "00"),
    (0x0E, "engine.timing_advance", 0.0, "80"),
    (0x0E, "engine.timing_advance", 10.0, "94"),
    (0x0E, "engine.timing_advance", 63.5, "ff"),
    # Intake air temperature, A - 40
    (0x0F, "engine.intake_temp", -40.0, "00"),
    (0x0F, "engine.intake_temp", 25.0, "41"),
    # Mass air flow rate, (256A + B) / 100
    (0x10, "engine.maf", 0.0, "0000"),
    (0x10, "engine.maf", 3.5, "015e"),
    (0x10, "engine.maf", 655.35, "ffff"),
    # Throttle position, A * 100 / 255
    (0x11, "engine.throttle", 0.0, "00"),
    (0x11, "engine.throttle", 14.0, "23"),
    (0x11, "engine.throttle", 100.0, "ff"),
    # OBD standards, coded
    (0x1C, "vehicle.obd_standard", 1, "01"),
    (0x1C, "vehicle.obd_standard", 11, "0b"),
    # Run time since engine start, 256A + B seconds
    (0x1F, "engine.runtime", 0, "0000"),
    (0x1F, "engine.runtime", 600, "0258"),
    (0x1F, "engine.runtime", 65535, "ffff"),
    # Fuel tank level input, A * 100 / 255
    (0x2F, "engine.fuel_level", 0, "00"),
    (0x2F, "engine.fuel_level", 50, "7f"),
    (0x2F, "engine.fuel_level", 100, "ff"),
    # Control module voltage, (256A + B) / 1000
    (0x42, "vehicle.battery_voltage", 0.0, "0000"),
    (0x42, "vehicle.battery_voltage", 12.6, "3138"),
    (0x42, "vehicle.battery_voltage", 14.1, "3714"),
    # Ambient air temperature, A - 40
    (0x46, "vehicle.ambient_temp", -40.0, "00"),
    (0x46, "vehicle.ambient_temp", 20.0, "3c"),
    # Fuel type, coded
    (0x51, "engine.fuel_type", 1, "01"),
    (0x51, "engine.fuel_type", 23, "17"),
]


@pytest.mark.parametrize("pid, signal, value, expected", BOUNDARIES)
def test_parameter_boundary_encodings(pid, signal, value, expected):
    vehicle = ice()
    vehicle.set(signal, value)
    assert MODE01_PIDS[pid].read(vehicle).hex() == expected


def test_every_defined_parameter_has_boundary_coverage():
    covered = {pid for pid, *_ in BOUNDARIES}
    assert covered == set(MODE01_PIDS), f"missing boundary tests for {sorted(set(MODE01_PIDS) - covered)}"


@pytest.mark.parametrize("pid", sorted(MODE01_PIDS))
def test_every_parameter_encodes_to_its_declared_length(pid):
    assert len(MODE01_PIDS[pid].read(ice())) == MODE01_PIDS[pid].length
