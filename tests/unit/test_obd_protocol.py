"""ObdProtocol: modes it serves, what it refuses, and that reads observe state."""

import pytest

from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.obd import ObdProtocol
from ecu_simulator.vehicle import CommonState, IcePowertrain, IceState, VehicleState


def vehicle(**engine):
    engine = {"coolant_temp": 90.0, "fuel_level": 50, **engine}
    return VehicleState(CommonState(vin="TESTVIN0123456789"), IcePowertrain(engine=IceState(**engine)))


def protocol(dtcs=("B1477", "P0001"), name="ECU_SIMULATOR", **engine):
    return ObdProtocol(vehicle(**engine), ecu_name=name, dtcs=list(dtcs))


def ask(proto, hex_request):
    return proto.handle(ServiceRequest(bytes.fromhex(hex_request)))


# --- claimed services ---------------------------------------------------------------------


def test_modes_01_to_0a_are_claimed_so_unimplemented_ones_stay_silent():
    # DEV-11: claiming them keeps the ECU's unsupported-service policy away from OBD.
    assert ObdProtocol.service_ids == frozenset(range(0x01, 0x0B))


@pytest.mark.parametrize("request_hex", ["04", "05", "06", "07", "08", "0a", "0200"])
def test_unimplemented_modes_answer_with_silence(request_hex):
    assert ask(protocol(), request_hex) is None


@pytest.mark.parametrize("request_hex", ["00", "0b", "22", "ff"])
def test_modes_outside_the_claimed_range_answer_with_silence(request_hex):
    assert ask(protocol(), request_hex) is None


# --- mode 01 ------------------------------------------------------------------------------


def test_a_supported_parameter_is_answered_with_its_encoded_value():
    assert ask(protocol(), "0105") == b"\x41\x05\x82"
    assert ask(protocol(), "012f") == b"\x41\x2f\x7f"


def test_an_unsupported_parameter_gets_no_response():
    assert ask(protocol(), "0199") is None
    assert ask(protocol(), "0101") is None  # monitor status, deferred


def test_mode_01_without_a_parameter_gets_no_response():
    assert ask(protocol(), "01") is None


def test_only_the_first_parameter_byte_is_read():
    # DEV-18: a multi-parameter request answers the first only.
    assert ask(protocol(), "01052f51") == b"\x41\x05\x82"


def test_a_range_request_is_answered_with_the_computed_mask():
    # 04-07, 0B-11, 1C and 1F are supported on a fully specified ICE vehicle.
    assert ask(protocol(), "0100") == bytes.fromhex("41001e3f8013")


def test_reading_the_same_parameter_twice_gives_the_same_answer():
    proto = protocol()
    assert ask(proto, "010d") == ask(proto, "010d")
    assert ask(proto, "0105") == ask(proto, "0105")


def test_handling_a_request_does_not_mutate_the_vehicle():
    proto = protocol()
    before = dict(proto.vehicle.signals)
    for request in ("0100", "0105", "010d", "012f", "03", "0902", "090a"):
        ask(proto, request)
    assert dict(proto.vehicle.signals) == before


# --- mode 03 --------------------------------------------------------------------------------


def test_stored_dtcs_are_counted_and_encoded():
    assert ask(protocol(), "03") == bytes.fromhex("430294770001")


def test_no_stored_dtcs_gives_a_zero_count():
    assert ask(protocol(dtcs=()), "03") == b"\x43\x00"


def test_a_trailing_request_byte_is_echoed_after_the_service_id():
    # DEV-15, preserved deliberately.
    assert ask(protocol(), "0300") == bytes.fromhex("43000294770001")


def test_dtcs_come_from_this_ecu_not_a_global():
    assert ask(protocol(dtcs=("P0100",)), "03") == bytes.fromhex("43010100")


# --- mode 09 --------------------------------------------------------------------------------


def test_vin_comes_from_the_vehicle_state():
    assert ask(protocol(), "0902") == b"\x49\x02\x00" + b"TESTVIN0123456789"


def test_a_short_vin_is_left_padded_to_seventeen_bytes():
    proto = protocol()
    proto.vehicle.set("vehicle.vin", "SHORT")
    response = ask(proto, "0902")
    assert response == b"\x49\x02\x00" + bytes(12) + b"SHORT"
    assert len(response) == 20


def test_ecu_name_comes_from_this_ecus_configuration():
    assert ask(protocol(name="TCM_SIMULATOR"), "090a") == b"\x49\x0a" + bytes(7) + b"TCM_SIMULATOR"


def test_mode09_range_request_is_answered():
    # Mode 09 defines nothing above 0x0D, so no next range is claimed (DEV-04).
    assert ask(protocol(), "0900") == bytes.fromhex("490040400000")
    assert ask(protocol(), "0920") is None


def test_an_unknown_mode09_parameter_gets_no_response():
    assert ask(protocol(), "0901") is None
    assert ask(protocol(), "09") is None
