"""Golden tests for the legacy UDS service layer (uds/services.py).

Expected values captured from commit ce46b87 with the shipped ecu_config.json
(DTCs B1477 and P0001). Plain tests pin today's bytes; xfail(strict=True) tests assert
the corrected behavior for a known deviation listed in docs/known-deviations.md.
"""

import pytest

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.transport import DiagnosticRequest
from ecu_simulator.uds import services
from tests.characterization.conftest import xfail_deviation


def uds(hex_request):
    return services.process_service_request(bytes.fromhex(hex_request))


def engine_uds(hex_request):
    """The same request as the wire sees it: physically addressed to the engine ECU's UDS id."""
    config = app.RuntimeConfig.build(load_profile(default_profile_path()))
    dispatcher = app.build_dispatcher(config)
    response = dispatcher(DiagnosticRequest(bytes.fromhex(hex_request), 0x7E1))
    return response.payload if response is not None else None


# --- 0x10 DiagnosticSessionControl ---------------------------------------------------------


@pytest.mark.parametrize("session", ["01", "02", "03", "04"])
def test_0x10_positive_response_with_fixed_session_parameter_record(session):
    # DEV-17: P2 = 0x001E (30 ms), P2* = 0x0BB8 (3000 x 10 ms); no session state is kept.
    assert uds("10" + session).hex() == "50" + session + "001e0bb8"


@pytest.mark.parametrize(
    "request_hex, expected", [("1005", "7f1012"), ("1000", "7f1012"), ("10", "7f1013"), ("100100", "7f1013")]
)
def test_0x10_negative_responses(request_hex, expected):
    assert uds(request_hex).hex() == expected


def test_0x10_suppress_positive_response_bit_today_is_rejected():
    assert uds("1083").hex() == "7f1012"


@xfail_deviation("DEV-07", "suppressPosRspMsgIndicationBit is not masked")
def test_0x10_suppress_positive_response_bit_corrected():
    assert uds("1083") is None


# --- 0x11 ECUReset -----------------------------------------------------------------------


@pytest.mark.parametrize("reset_type", ["01", "02", "03", "05"])
def test_0x11_positive_response_echoes_reset_type(reset_type):
    assert uds("11" + reset_type).hex() == "51" + reset_type


def test_0x11_enable_rapid_power_shutdown_appends_power_down_time():
    assert uds("1104").hex() == "51040f"


@pytest.mark.parametrize(
    "request_hex, expected", [("1106", "7f1112"), ("1100", "7f1112"), ("11", "7f1113"), ("110100", "7f1113")]
)
def test_0x11_negative_responses(request_hex, expected):
    assert uds(request_hex).hex() == expected


def test_0x11_suppress_positive_response_bit_today_is_rejected():
    assert uds("1181").hex() == "7f1112"


@xfail_deviation("DEV-07", "suppressPosRspMsgIndicationBit is not masked")
def test_0x11_suppress_positive_response_bit_corrected():
    assert uds("1181") is None


# --- 0x19 ReadDTCInformation ----------------------------------------------------------------


def test_0x19_02_without_status_mask_returns_all_dtcs_with_fixed_status():
    # DEV-05 and DEV-16: 59 02 FF, then per DTC: 2-byte J2012 code, 0x01, status 0x2F.
    assert uds("1902").hex() == "5902ff" + "9477012f" + "0001012f"


def test_0x19_02_with_status_mask_today_gets_incorrect_length_nrc():
    assert uds("1902ff").hex() == "7f1913"
    assert uds("190200").hex() == "7f1913"


@xfail_deviation("DEV-05", "0x19 0x02 rejects the DTCStatusMask byte")
def test_0x19_02_with_status_mask_corrected_is_answered_positively():
    response = uds("1902ff")
    assert response is not None
    assert response[:2] == b"\x59\x02"


@pytest.mark.parametrize(
    "request_hex, expected", [("1901", "7f1912"), ("190a", "7f1912"), ("1900", "7f1912"), ("19", "7f1913")]
)
def test_0x19_negative_responses(request_hex, expected):
    assert uds(request_hex).hex() == expected


def test_0x19_02_with_empty_dtc_list_returns_header_only(monkeypatch):
    monkeypatch.setattr(services, "DTCS", bytearray())
    assert uds("1902").hex() == "5902ff"


# --- Unsupported services and malformed input ------------------------------------------------


@pytest.mark.parametrize("request_hex", ["22f190", "3e00", "3e80", "14ffffff", "2701", "2e", "3101", "7f", "50", "ff"])
def test_legacy_uds_module_ignores_unsupported_sids(request_hex):
    # The legacy layer is unchanged; since DEV-06 these SIDs never reach it (see below).
    assert uds(request_hex) is None


@pytest.mark.parametrize("request_hex, expected", [("22f190", "7f2211"), ("2701", "7f2711"), ("3101", "7f3111")])
def test_unsupported_sids_on_the_physical_address_get_nrc_0x11(request_hex, expected):
    # DEV-06 corrected in Phase 3 by the ECU's unsupported-service policy, not by the
    # legacy module: a SID no registered protocol claims gets 7F <SID> 11 on a physical address.
    assert engine_uds(request_hex).hex() == expected


@xfail_deviation("DEV-23", "0x3E TesterPresent is not implemented")
def test_0x3e_tester_present_corrected():
    assert uds("3e00").hex() == "7e00"


@xfail_deviation("DEV-23", "0x14 ClearDiagnosticInformation is not implemented")
def test_0x14_clear_diagnostic_information_corrected():
    assert uds("14ffffff") == b"\x54"


def test_empty_and_none_requests_get_no_response():
    assert services.process_service_request(b"") is None
    assert services.process_service_request(None) is None


# --- Framing helpers ---------------------------------------------------------------------------


def test_positive_response_sid_adds_0x40():
    assert services.get_positive_response_sid(0x10) == b"\x50"
    assert services.get_positive_response_sid(0x3E) == b"\x7e"


def test_negative_response_framing():
    assert services.get_negative_response(0x22, 0x31) == b"\x7f\x22\x31"
