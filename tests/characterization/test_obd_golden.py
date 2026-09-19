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
    return ObdProtocol(app.build_vehicle(config), ecu_name=ecu.name, dtcs=ecu.dtcs)


def obd(sid, pid=None):
    payload = bytes([sid]) if pid is None else bytes([sid, pid])
    return protocol().handle(ServiceRequest(payload))


# --- Mode 01 supported-PID masks -------------------------------------------------------


@pytest.mark.parametrize(
    "pid, expected",
    [
        (0x00, "410008080001"),  # PIDs 05, 0D supported; bit 0 claims 0x20 range (0x2F exists)
        (0x20, "412000020001"),  # PID 2F supported; bit 0 claims 0x40 range (0x51 exists)
        (0x40, "414000008001"),  # PID 51 supported; bit 0 claims 0x60 range (nothing there, DEV-04)
        (0x60, "416000000001"),  # DEV-04
        (0x80, "418000000001"),  # DEV-04
        (0xA0, "41a000000001"),  # DEV-04
        (0xC0, "41c000000001"),  # DEV-04
        (0xE0, "41e000000000"),
    ],
)
def test_mode01_supported_pid_masks_today(pid, expected):
    assert obd(0x01, pid).hex() == expected


@xfail_deviation("DEV-04", "continuation bit set although no PIDs exist in the next range")
@pytest.mark.parametrize(
    "pid, expected",
    [
        (0x40, "414000008000"),
        (0x60, "416000000000"),
        (0x80, "418000000000"),
    ],
)
def test_mode01_supported_pid_masks_corrected(pid, expected):
    assert obd(0x01, pid).hex() == expected


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


@pytest.mark.parametrize(
    "pid", [0x01, 0x04, 0x06, 0x07, 0x0B, 0x0C, 0x0E, 0x0F, 0x10, 0x11, 0x1C, 0x1F, 0x42, 0x46, 0xFF]
)
def test_mode01_unsupported_pids_get_no_response(pid):
    assert obd(0x01, pid) is None


@xfail_deviation("DEV-12", "Mode 01 PID 0C engine RPM is not implemented")
def test_mode01_pid0c_rpm_is_answered():
    response = obd(0x01, 0x0C)
    assert response is not None
    assert response[:2] == b"\x41\x0c" and len(response) == 4


def test_mode01_without_pid_gets_no_response():
    assert obd(0x01, None) is None


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


def test_mode09_supported_pid_mask_today():
    # PIDs 02 and 0A supported; bit 0 claims the 0x20 range although nothing exists (DEV-04)
    assert obd(0x09, 0x00).hex() == "490040400001"
    assert obd(0x09, 0x20).hex() == "492000000001"


@xfail_deviation("DEV-04", "Mode 09 mask claims PIDs exist in the 0x20 range")
def test_mode09_supported_pid_mask_corrected():
    assert obd(0x09, 0x00).hex() == "490040400000"


def test_mode09_pid02_vin_today_has_item_count_zero():
    response = obd(0x09, 0x02)
    assert response == b"\x49\x02\x00" + VIN_BYTES
    assert len(response) == 20


@xfail_deviation("DEV-02", "VIN item-count byte is 0x00 instead of 0x01")
def test_mode09_pid02_vin_corrected_item_count():
    assert obd(0x09, 0x02) == b"\x49\x02\x01" + VIN_BYTES


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
