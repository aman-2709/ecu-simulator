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
    assert masks.supported_mask(0x00, {0x05, 0x0D}).hex() == "08080001"
    assert masks.supported_mask(0x20, {0x2F}).hex() == "00020001"
    assert masks.supported_mask(0x40, {0x51}).hex() == "00008001"


def test_mask_ignores_identifiers_outside_its_range():
    assert masks.supported_mask(0x00, {0x2F, 0x51}).hex() == "00000001"


def test_the_last_range_never_claims_a_successor():
    assert masks.supported_mask(0xE0, set()).hex() == "00000000"


def test_range_requests_are_recognised():
    assert [p for p in range(0x100) if masks.is_range_request(p)] == list(range(0x00, 0x100, 0x20))
    assert masks.range_bases() == (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0)
