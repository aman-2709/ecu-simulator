"""Golden tests for the legacy DTC encoder (dtc_utils.py).

The SAE J2012 two-byte encoding here is believed correct and is preserved through the
modernization. The UDS three-byte-plus-status layout and the silent skipping of malformed
strings are recorded as DEV-16 and DEV-13 in docs/known-deviations.md.
"""
import pytest

import dtc_utils


@pytest.mark.parametrize(
    "dtc, expected",
    [
        ("P0001", "0001"),
        ("P0301", "0301"),
        ("P3FFF", "3fff"),
        ("C1234", "5234"),
        ("B1477", "9477"),
        ("U0100", "c100"),
        ("U3FFF", "ffff"),
        ("p0001", ""),  # lowercase letter is not a valid group
    ],
)
def test_obd_two_byte_encoding(dtc, expected):
    assert dtc_utils.encode_obd_dtcs([dtc]).hex() == expected


def test_obd_encoding_concatenates_in_config_order():
    assert dtc_utils.encode_obd_dtcs(["B1477", "P0001"]).hex() == "94770001"


@pytest.mark.parametrize("dtc", ["P4001", "X0001", "P000", "P00001", "P00G1", "", "PO001"])
def test_malformed_dtcs_are_silently_skipped(dtc):
    # DEV-13: no error is raised; the entry simply disappears from the response.
    assert dtc_utils.encode_obd_dtcs([dtc]) == bytearray()
    assert dtc_utils.is_dtc_valid(dtc) is False


def test_mixed_valid_and_malformed_list_keeps_only_valid_entries():
    dtcs = ["P0001", "C1234", "B1477", "U3FFF", "P4001", "X0001", "P000", "P00001"]
    assert dtc_utils.encode_obd_dtcs(dtcs).hex() == "000152349477ffff"


def test_uds_encoding_appends_fixed_third_byte_and_status():
    # DEV-16: third byte is always 0x01 and status is always 0x2F.
    assert dtc_utils.encode_uds_dtcs(["B1477", "P0001"]).hex() == "9477012f" + "0001012f"


def test_uds_encoding_of_empty_list_is_empty():
    assert dtc_utils.encode_uds_dtcs([]) == bytearray()


def test_group_and_type_bit_tables():
    assert dtc_utils.DTC_GROUP == {"P": "00", "C": "01", "B": "10", "U": "11"}
    assert dtc_utils.DTC_TYPE == {"0": "00", "1": "01", "2": "10", "3": "11"}
