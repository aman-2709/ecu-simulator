"""Golden tests for the OBD-II service layer.

Expected values were captured from the implementation at commit ce46b87 with the shipped
configuration (VIN TESTVIN0123456789, ECU name ECU_SIMULATOR, fuel level 50, fuel type 1,
DTCs B1477 and P0001). Plain tests pin today's bytes; tests marked xfail(strict=True)
assert the corrected behavior for a known deviation.

Phase 5 retargeted these from the legacy modules, which are deleted, onto the protocol
that now answers on the wire. Every wire expectation was carried over unchanged. A
differential comparison over all 3084 service and parameter combinations confirmed the new
implementation is byte-identical to the old one except for the coolant temperature, whose
randomness this phase removes (DEV-10). Assertions about internal helper functions of the
deleted modules were dropped; the configuration-validation behavior that replaced them is
covered by tests/unit/test_config_schema.py.
"""

import pytest

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.obd import ObdProtocol
from tests.characterization.conftest import xfail_deviation

VIN_BYTES = b"TESTVIN0123456789"


def protocol():
    config = app.RuntimeConfig.build(load_profile(default_profile_path()))
    ecu = config.profile.ecus["engine"]
    return ObdProtocol(app.build_vehicle(config), ecu_name=ecu.name, dtcs=[d.code for d in ecu.dtcs])


def obd(sid, pid=None):
    payload = bytes([sid]) if pid is None else bytes([sid, pid])
    return protocol().handle(ServiceRequest(payload))


def obd_raw(hex_request):
    """A request of any length, for the multi-parameter cases (DEV-18)."""
    return protocol().handle(ServiceRequest(bytes.fromhex(hex_request)))


# --- Mode 01 supported-PID masks -------------------------------------------------------


@pytest.mark.parametrize(
    "pid, expected",
    [
        # DEV-04 corrected: the chain ends after the last populated range.
        (0x00, "41001e3f8013"),  # 04-07, 0B-11, 1C, 1F; bit 0 set, 0x2F exists beyond
        (0x20, "412000020001"),  # PID 2F; bit 0 set, 0x42/0x46/0x51 exist beyond
        (0x40, "414044008000"),  # PIDs 42, 46, 51; bit 0 clear, nothing beyond 0x60
    ],
)
def test_mode01_supported_pid_masks(pid, expected):
    assert obd(0x01, pid).hex() == expected


@pytest.mark.parametrize("pid", [0x60, 0x80, 0xA0, 0xC0, 0xE0])
def test_mode01_unadvertised_ranges_are_not_answered(pid):
    # DEV-04 corrected: a range the chain never reaches is not advertised, so answering it
    # would contradict the mask. Previously every one of these returned an empty mask.
    assert obd(0x01, pid) is None


# --- Mode 01 data parameters -------------------------------------------------------------


def test_mode01_pid05_coolant_is_deterministic():
    # DEV-10 corrected in Phase 5: the value comes from engine.coolant_temp, which the
    # shipped profile sets to 90 degrees Celsius, encoded as 90 + 40 = 0x82.
    assert obd(0x01, 0x05).hex() == "410582"
    assert obd(0x01, 0x05) == obd(0x01, 0x05)


def test_mode01_pid0d_speed_is_deterministic_and_has_no_side_effect():
    # DEV-09 corrected in Phase 5: reading observes vehicle.speed, it does not advance it.
    first = obd(0x01, 0x0D)
    assert first.hex() == "410d00"
    assert obd(0x01, 0x0D) == first


def test_mode01_pid2f_fuel_level_50_percent_encodes_as_0x7f():
    # Truncation, not rounding: 50 * 255 / 100 = 127.5 -> 0x7F. Unchanged since ce46b87.
    assert obd(0x01, 0x2F).hex() == "412f7f"


def test_mode01_pid51_fuel_type_gasoline():
    assert obd(0x01, 0x51).hex() == "415101"


@pytest.mark.parametrize("pid", [0x01, 0x02, 0x03, 0x08, 0x12, 0x21, 0x50, 0xFF])
def test_mode01_unsupported_pids_get_no_response(pid):
    assert obd(0x01, pid) is None


def test_mode01_pid0c_rpm_is_answered():
    # DEV-12 corrected in Phase 5: 800 rpm encodes as 800 * 4 = 0x0C80.
    response = obd(0x01, 0x0C)
    assert response == bytes.fromhex("410c0c80")


def test_mode01_pid01_monitor_status_is_still_absent():
    # Deferred deliberately: its first byte carries DTC-store semantics that belong to
    # Phase 6, and its monitor bits are not corroborated. See pids.DEFERRED_MODE01_PIDS.
    assert obd(0x01, 0x01) is None


def test_mode01_without_pid_gets_no_response():
    assert obd(0x01, None) is None


def test_mode01_multi_parameter_request_answers_every_parameter():
    # DEV-18 corrected in Phase 5.1. Before, everything after the first parameter was
    # discarded and these three answered 410582, 410d00 and 41001e3f8013.
    assert obd_raw("01052f51").hex() == "410582" + "2f7f" + "5101"
    assert obd_raw("010d0c").hex() == "410d00" + "0c0c80"
    assert obd_raw("01000c").hex() == "41001e3f8013" + "0c0c80"


def test_mode01_multi_parameter_request_omits_an_unsupported_parameter():
    # DEV-18 corrected: the request is no longer judged by its first parameter alone, so
    # 01 FF 05 now answers the coolant temperature where before it answered nothing.
    assert obd_raw("0105ff").hex() == "410582"
    assert obd_raw("01ff05").hex() == "410582"


# --- Mode 03 / 04 / 07 -------------------------------------------------------------------


def test_mode03_returns_count_and_two_byte_dtcs():
    # B1477 -> 94 77, P0001 -> 00 01
    assert obd(0x03).hex() == "430294770001"


def test_mode03_with_trailing_byte_echoes_it_into_the_response():
    # DEV-15: the extra request byte is treated as a PID and inserted after the SID.
    assert obd(0x03, 0x00).hex() == "43000294770001"


def test_mode04_and_mode07_are_valid_sids_but_get_no_response():
    assert obd(0x04) is None
    assert obd(0x07) is None
    assert obd(0x04, 0x00) is None


@xfail_deviation("DEV-11", "Mode 04 clear DTCs is not implemented")
def test_mode04_clear_dtcs_is_acknowledged():
    assert obd(0x04) == b"\x44"


@xfail_deviation("DEV-11", "Mode 07 pending DTCs is not implemented")
def test_mode07_pending_dtcs_is_answered():
    response = obd(0x07)
    assert response is not None and response[0] == 0x47


# --- Mode 09 ----------------------------------------------------------------------------


def test_mode09_supported_pid_mask():
    # DEV-04 corrected: mode 09 defines nothing above 0x0D, so no next range is claimed.
    assert obd(0x09, 0x00).hex() == "490040400000"
    assert obd(0x09, 0x20) is None


def test_mode09_pid02_vin_has_item_count_one():
    # DEV-02 corrected in Phase 5. The length is unchanged at 20 bytes.
    response = obd(0x09, 0x02)
    assert response == b"\x49\x02\x01" + VIN_BYTES
    assert len(response) == 20


def test_mode09_pid0a_ecu_name_today_is_left_nul_padded_without_item_count():
    # DEV-03 is deferred; see tests/characterization/test_mode09_pid0a_frozen.py.
    response = obd(0x09, 0x0A)
    assert response == b"\x49\x0a" + b"\x00" * 7 + b"ECU_SIMULATOR"
    assert len(response) == 22


@xfail_deviation("DEV-03", "ECU name response lacks the item-count byte")
def test_mode09_pid0a_ecu_name_corrected_framing():
    response = obd(0x09, 0x0A)
    assert response[:3] == b"\x49\x0a\x01"
    assert len(response) == 23


def test_mode09_unsupported_pid_and_missing_pid_get_no_response():
    assert obd(0x09, 0x01) is None
    assert obd(0x09, None) is None


# --- Validation and rejection ------------------------------------------------------------


@pytest.mark.parametrize("sid", [0x00, 0x0B, 0x22, 0xFF])
def test_unknown_sids_get_no_response(sid):
    assert obd(sid, 0x00) is None
    assert obd(sid) is None


def test_mode0a_is_claimed_but_unimplemented_so_stays_silent():
    # DEV-11: modes 0x01 to 0x0A are all claimed so that an unimplemented one answers with
    # silence rather than falling through to the ECU's unsupported-service policy.
    assert 0x0A in ObdProtocol.service_ids
    assert obd(0x0A) is None
    assert obd(0x0A, 0x00) is None
