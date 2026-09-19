"""ObdProtocol: modes it serves, what it refuses, and that reads observe state."""

import pytest

from ecu_simulator.dtc import DtcState, DtcStore
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.obd import ObdProtocol
from ecu_simulator.vehicle import CommonState, IcePowertrain, IceState, VehicleState


def vehicle(**engine):
    engine = {"coolant_temp": 90.0, "fuel_level": 50, **engine}
    return VehicleState(CommonState(vin="TESTVIN0123456789"), IcePowertrain(engine=IceState(**engine)))


def store(*codes, pending=True, confirmed=True):
    return DtcStore(DtcState(code, pending=pending, confirmed=confirmed) for code in codes)


def protocol(dtcs=("B1477", "P0001"), name="ECU_SIMULATOR", **engine):
    dtcs = dtcs if isinstance(dtcs, DtcStore) else store(*dtcs)
    return ObdProtocol(vehicle(**engine), ecu_name=name, dtcs=dtcs)


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


# --- mode 01, several parameters in one request (DEV-18) ----------------------------------
#
# The two captures below are the worked CAN examples published in the ELM327 datasheet,
# "Multiple PID Requests". The test vehicle is configured to the physical values those
# captures encode, so the assertions are the datasheet's bytes and not our own arithmetic
# repeated back. See docs/decisions/0005-phase-5-1-multi-pid-evidence.md.


def datasheet_vehicle():
    """Engine load 0x3F, coolant 0x44, manifold pressure 0x21, 1518 rpm = 0x17B8."""
    return protocol(engine_load=24.8, coolant_temp=28.0, map=33, rpm=1518)


def test_the_datasheet_capture_is_reproduced_byte_for_byte():
    assert ask(datasheet_vehicle(), "0104050b0c").hex() == "4104" "3f" "05" "44" "0b" "21" "0c" "17b8"


def test_the_datasheet_capture_in_a_different_order_answers_in_that_order():
    # The datasheet says the response order need not match the request order; it answers
    # in request order in its own capture, and so does this simulator.
    assert ask(datasheet_vehicle(), "010b040c05").hex() == "410b" "21" "04" "3f" "0c" "17b8" "05" "44"


def test_a_single_parameter_request_is_unchanged():
    assert ask(protocol(), "0105") == b"\x41\x05\x82"
    assert ask(protocol(), "012f") == b"\x41\x2f\x7f"


def test_at_most_six_parameters_are_answered():
    # Project choice: the parser ignored everything after the first parameter; it now
    # ignores everything after the sixth. Seven are requested, six are answered, and the
    # intake temperature asked for in seventh place is absent.
    response = ask(datasheet_vehicle(), "01" + "04050b0c0d0e0f")
    assert response.hex() == "4104" "3f" "05" "44" "0b" "21" "0c" "17b8" "0d" "00" "0e" "80"


def test_an_unsupported_parameter_is_omitted_and_the_rest_are_answered():
    assert ask(protocol(), "0105ff2f").hex() == "4105" "82" "2f" "7f"


def test_a_request_of_only_unsupported_parameters_gets_no_response():
    assert ask(protocol(), "01ff99") is None


def test_a_repeated_parameter_is_answered_once_in_its_first_position():
    assert ask(datasheet_vehicle(), "010c0c").hex() == "410c17b8"
    assert ask(datasheet_vehicle(), "012f052f").hex() == "412f" "7f" "05" "44"


def test_a_range_identifier_may_be_requested_alongside_data_parameters():
    assert ask(datasheet_vehicle(), "01000c").hex() == "4100" "1e3f8013" "0c" "17b8"


def test_an_unadvertised_range_identifier_is_omitted_like_any_unsupported_parameter():
    # DEV-04: the chain never reaches 0x60, so it is not answered here either.
    assert ask(datasheet_vehicle(), "0160") is None
    assert ask(datasheet_vehicle(), "01600c").hex() == "410c17b8"


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


def test_mode03_reports_the_confirmed_codes_only():
    mixed = DtcStore(
        [
            DtcState("P0001", pending=True, confirmed=True),
            DtcState("P0002", pending=True),
            DtcState("P0003", confirmed=True),
        ]
    )
    assert ask(protocol(dtcs=mixed), "03") == bytes.fromhex("4302" "0001" "0003")


def test_mode03_answers_a_zero_count_when_nothing_is_confirmed():
    pending_only = DtcStore([DtcState("P0001", pending=True)])
    assert ask(protocol(dtcs=pending_only), "03") == b"\x43\x00"


def test_mode03_follows_the_store_after_it_is_cleared():
    proto = protocol()
    assert ask(proto, "03") == bytes.fromhex("430294770001")
    proto.dtcs.clear()
    assert ask(proto, "03") == b"\x43\x00"


# --- mode 09 --------------------------------------------------------------------------------


def test_vin_comes_from_the_vehicle_state():
    assert ask(protocol(), "0902") == b"\x49\x02\x01" + b"TESTVIN0123456789"


def test_the_vin_item_count_is_one_and_the_length_is_unchanged():
    # DEV-02: only the third byte changed; the response is still 20 bytes.
    response = ask(protocol(), "0902")
    assert response[:3] == b"\x49\x02\x01"
    assert response[2] == 1
    assert len(response) == 20


def test_a_short_vin_is_left_padded_to_seventeen_bytes():
    proto = protocol()
    proto.vehicle.set("vehicle.vin", "SHORT")
    response = ask(proto, "0902")
    assert response == b"\x49\x02\x01" + bytes(12) + b"SHORT"
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
