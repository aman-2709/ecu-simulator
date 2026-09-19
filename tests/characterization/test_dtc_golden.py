"""Golden tests for the diagnostic trouble code encoders.

The SAE J2012 two-byte encoding is believed correct and is preserved through the
modernization. Phase 6 retargeted these from dtc_utils.py, which is deleted, onto the
modules that now produce the bytes: the code-to-number arithmetic both protocols share,
the OBD service 03 framing and the UDS record framing. Every expectation is carried over
unchanged.

The UDS third byte 0x01 and the fixed status 0x2F are DEV-16. Phase 6 derives the status
from the shared store in a later commit; the third byte stays frozen, because no evidence
supports any value for it, including that one. What used to be DEV-13, malformed strings
silently skipped, has been configuration-load validation since Phase 4; these tests keep
pinning what the encoder itself accepts.
"""

import pytest

from ecu_simulator.dtc import DtcState, DtcStore, code_number, is_valid
from ecu_simulator.protocols.obd import dtc as obd_dtc
from ecu_simulator.protocols.uds import DtcRegistry, DtcStoreProvider
from ecu_simulator.protocols.uds import dtc as uds_dtc


def obd_bytes(*codes):
    """Service 03 body without its count byte, so the pinned values are unchanged."""
    encoded = obd_dtc.encode(DtcState(code, confirmed=True) for code in codes if is_valid(code))
    return encoded[1:] if encoded != bytes(1) else b""


def uds_records(*codes):
    providers = DtcRegistry()
    providers.register(DtcStoreProvider("engine", DtcStore(DtcState(code) for code in codes)))
    return b"".join(record.to_bytes() for record in providers.read(0xFF))


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
    assert obd_bytes(dtc).hex() == expected


def test_obd_encoding_concatenates_in_config_order():
    assert obd_bytes("B1477", "P0001").hex() == "94770001"


@pytest.mark.parametrize("dtc", ["P4001", "X0001", "P000", "P00001", "P00G1", "", "PO001"])
def test_malformed_dtcs_are_not_encoded(dtc):
    # Was DEV-13: the encoder skipped them silently. Since Phase 4 the profile schema
    # rejects them at load, so the encoder never sees one; it still refuses them.
    assert obd_bytes(dtc) == b""
    assert is_valid(dtc) is False


def test_mixed_valid_and_malformed_list_keeps_only_valid_entries():
    dtcs = ["P0001", "C1234", "B1477", "U3FFF", "P4001", "X0001", "P000", "P00001"]
    assert obd_bytes(*dtcs).hex() == "000152349477ffff"


def test_uds_encoding_appends_fixed_third_byte_and_status():
    # DEV-16: third byte is always 0x01 and status is always 0x2F at this point.
    assert uds_records("B1477", "P0001").hex() == "9477012f" + "0001012f"


def test_uds_encoding_of_empty_list_is_empty():
    assert uds_records() == b""


def test_group_and_type_bit_tables():
    assert code_number("P0000") == 0x0000
    assert code_number("C0000") == 0x4000
    assert code_number("B0000") == 0x8000
    assert code_number("U0000") == 0xC000
    assert code_number("P1000") == 0x1000
    assert code_number("P2000") == 0x2000
    assert code_number("P3000") == 0x3000


def test_the_fixed_uds_bytes_are_named_constants_so_the_freeze_is_visible():
    assert uds_dtc.FAILURE_TYPE_BYTE == 0x01
    assert uds_dtc.FIXED_STATUS == 0x2F
