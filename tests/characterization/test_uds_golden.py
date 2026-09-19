"""Golden tests for the UDS service layer.

Expected values captured from commit ce46b87 with the shipped ecu_config.json
(DTCs B1477 and P0001). Plain tests pin today's bytes; xfail(strict=True) tests assert
the corrected behavior for a known deviation listed in docs/known-deviations.md.

Phase 6 retargeted these from uds/services.py, which is deleted, onto the protocol that
now answers on the wire. Every wire expectation was carried over unchanged. A differential
comparison over 16653 requests - each claimed service identifier and a set of unclaimed
ones, every second byte, every length from one to five - confirmed the replacement is
byte-identical to the module it replaced. The deliberate corrections that follow arrive in
their own commits and are marked where they land.
"""

import pytest

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.protocols.base import ServiceRequest, negative_response, positive_response_sid
from ecu_simulator.transport import DiagnosticRequest
from tests.characterization.conftest import xfail_deviation


def protocol():
    config = app.RuntimeConfig.build(load_profile(default_profile_path()))
    engine = app.build_ecus(config)[0]
    return engine.protocol_for(0x19)


def uds(hex_request):
    return protocol().handle(ServiceRequest(bytes.fromhex(hex_request)))


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


def test_0x19_a_three_byte_request_today_is_rejected_for_its_length_before_its_subfunction():
    # The length check runs first, so an unknown sub-function in a three-byte request is
    # never reached and the answer is 0x13 rather than 0x12. Correcting DEV-05 requires
    # three-byte requests to reach the sub-function check, which moves this response.
    assert uds("1982ff").hex() == "7f1913"


@xfail_deviation("NRC ordering", "the 0x19 length check runs before the sub-function check")
def test_0x19_an_unknown_subfunction_is_rejected_as_a_subfunction_whatever_the_length():
    assert uds("1982ff").hex() == "7f1912"


def test_0x19_02_with_empty_dtc_list_returns_header_only():
    from ecu_simulator.dtc import DtcStore
    from ecu_simulator.protocols.uds import DtcRegistry, DtcStoreProvider, UdsProtocol

    providers = DtcRegistry()
    providers.register(DtcStoreProvider("engine", DtcStore()))
    assert UdsProtocol(dtc_providers=providers).handle(ServiceRequest(b"\x19\x02")).hex() == "5902ff"


# --- Unsupported services and malformed input ------------------------------------------------


@pytest.mark.parametrize("request_hex", ["22f190", "3e00", "3e80", "14ffffff", "2701", "2e", "3101", "7f", "50", "ff"])
def test_the_uds_protocol_does_not_claim_these_service_identifiers(request_hex):
    # Since DEV-06 these never reach a protocol at all: the route's unsupported-service
    # policy answers them (see below). The protocol must not start claiming them.
    assert bytes.fromhex(request_hex)[0] not in protocol().service_ids


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


def test_an_empty_request_never_reaches_a_protocol():
    # ServiceRequest refuses to exist for an empty payload, and the ECU drops one before
    # building it; the legacy module used to return None for b"" and for None.
    with pytest.raises(ValueError):
        ServiceRequest(b"")
    assert engine_uds("") is None


# --- Framing helpers ---------------------------------------------------------------------------


def test_positive_response_sid_adds_0x40():
    # The helpers moved to protocols/base.py in Phase 3 and are shared with OBD; the
    # legacy module's own copies went with it in Phase 6. Same arithmetic, same bytes.
    assert bytes([positive_response_sid(0x10)]) == b"\x50"
    assert bytes([positive_response_sid(0x3E)]) == b"\x7e"


def test_negative_response_framing():
    assert negative_response(0x22, 0x31) == b"\x7f\x22\x31"
