"""Golden tests for the legacy OBD-II service layer (obd/services.py, obd/responses.py).

Every expected value below was captured from the implementation at commit ce46b87 with
the shipped ecu_config.json (VIN TESTVIN0123456789, ECU name ECU_SIMULATOR, fuel level
50, fuel type 1, DTCs B1477 and P0001). Plain tests pin today's bytes; tests marked
xfail(strict=True) assert the corrected behavior for a known deviation.
"""
import random

import pytest

from ecu_simulator.obd import responses, services
from tests.characterization.conftest import xfail_deviation

VIN_BYTES = b"TESTVIN0123456789"


def obd(sid, pid=None):
    return services.process_service_request(requested_sid=sid, requested_pid=pid)


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
        (0xA0, "41a000000000"),
        (0xC0, "41c000000000"),
    ],
)
def test_mode01_supported_pid_masks_corrected(pid, expected):
    assert obd(0x01, pid).hex() == expected


# --- Mode 01 data PIDs -----------------------------------------------------------------

def test_mode01_pid05_coolant_is_random_in_130_to_149(reset_speed):
    for _ in range(50):
        response = obd(0x01, 0x05)
        assert response[:2] == b"\x41\x05"
        assert len(response) == 3
        assert 130 <= response[2] <= 149


def test_mode01_pid05_coolant_depends_on_random_module(monkeypatch):
    monkeypatch.setattr(random, "randrange", lambda lo, hi: 0xAB)
    assert obd(0x01, 0x05).hex() == "4105ab"


@xfail_deviation("DEV-10", "coolant temperature is drawn from random.randrange")
def test_mode01_pid05_coolant_does_not_use_random(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("random.randrange must not be used for coolant temperature")

    monkeypatch.setattr(random, "randrange", forbidden)
    response = obd(0x01, 0x05)
    assert response[:2] == b"\x41\x05" and len(response) == 3


def test_mode01_pid0d_speed_increments_on_every_read(reset_speed):
    assert obd(0x01, 0x0D).hex() == "410d00"
    assert obd(0x01, 0x0D).hex() == "410d01"
    assert obd(0x01, 0x0D).hex() == "410d02"


def test_mode01_pid0d_speed_wraps_after_255(reset_speed):
    responses.vehicle_speed = 255
    assert obd(0x01, 0x0D).hex() == "410dff"
    assert obd(0x01, 0x0D).hex() == "410d00"


@xfail_deviation("DEV-09", "reading vehicle speed has a side effect")
def test_mode01_pid0d_speed_read_has_no_side_effect(reset_speed):
    assert obd(0x01, 0x0D) == obd(0x01, 0x0D)


def test_mode01_pid2f_fuel_level_50_percent_encodes_as_0x7f():
    assert obd(0x01, 0x2F).hex() == "412f7f"


def test_mode01_pid51_fuel_type_gasoline():
    assert obd(0x01, 0x51).hex() == "415101"


@pytest.mark.parametrize("pid", [0x01, 0x04, 0x06, 0x07, 0x0B, 0x0C, 0x0E, 0x0F, 0x10, 0x11, 0x1C, 0x1F, 0x42, 0x46, 0xFF])
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

@pytest.mark.parametrize("sid", [0x00, 0x0A, 0x0B, 0x22, 0xFF])
def test_out_of_range_or_unknown_sids_get_no_response(sid):
    assert obd(sid, 0x00) is None
    assert obd(sid) is None


@pytest.mark.parametrize("sid, pid", [(0x01, 256), (0x01, -1), ("01", 0x00), (0x01, "0c"), (None, None), (1.0, 0)])
def test_non_integer_or_out_of_range_arguments_get_no_response(sid, pid):
    assert obd(sid, pid) is None


# --- responses.py helpers ----------------------------------------------------------------

def test_vin_shorter_than_17_is_left_nul_padded():
    assert responses.add_vin_padding("SHORT") == b"\x00" + b"\x00" * 12 + b"SHORT"


def test_vin_longer_than_17_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(responses.ecu_config, "get_vin", lambda: "X" * 18)
    assert responses.get_vin() == b"\x00" + VIN_BYTES


def test_ecu_name_longer_than_20_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(responses.ecu_config, "get_ecu_name", lambda: "N" * 21)
    assert responses.get_ecu_name() == b"\x00" * 7 + b"ECU_SIMULATOR"


@pytest.mark.parametrize("value, expected", [(0, 0), (100, 100), (101, 60), ("50", 60), (None, 60), (-5, -5)])
def test_fuel_level_validation_silently_substitutes_default_and_accepts_negatives(value, expected):
    # DEV-14: out-of-range values are replaced by the default 60, but negatives pass through.
    assert responses.validate_fuel_level(value) == expected


def test_negative_fuel_level_in_config_raises_at_request_time(monkeypatch):
    # DEV-14: -5 * 2.55 -> -12, which cannot be encoded as an unsigned byte.
    monkeypatch.setattr(responses.ecu_config, "get_fuel_level", lambda: -5)
    with pytest.raises(OverflowError):
        responses.get_fuel_level()


@pytest.mark.parametrize("value, expected", [(1, 1), (23, 23), (0, 1), (24, 1), (-1, 1)])
def test_fuel_type_validation_silently_substitutes_default(value, expected):
    assert responses.validate_fuel_type(value) == expected


def test_empty_dtc_list_encodes_as_single_zero_count_byte():
    assert responses.add_number_of_dtcs_to_response(bytearray()) == b"\x00"
