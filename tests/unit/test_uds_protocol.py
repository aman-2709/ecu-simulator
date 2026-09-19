"""UdsProtocol: the services that replaced the frozen legacy module.

Every expectation here is a byte-for-byte carry-over of what the legacy module answered.
The deliberate corrections that follow (DEV-05, DEV-16, DEV-23) arrive in their own
commits with their own tests; this file is the proof that replacing the module changed
nothing on its own.
"""

import pytest

from ecu_simulator.dtc import DtcState, DtcStore
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.uds import DtcRegistry, UdsProtocol
from ecu_simulator.protocols.uds.dtc import DtcStoreProvider

DEFAULT = (DtcState("B1477", pending=True, confirmed=True), DtcState("P0001", pending=True))


def protocol(entries=DEFAULT):
    store = DtcStore(entries)
    providers = DtcRegistry()
    providers.register(DtcStoreProvider("engine", store))
    return UdsProtocol(dtc_providers=providers), store


def ask(hex_request):
    proto, _ = protocol()
    return proto.handle(ServiceRequest(bytes.fromhex(hex_request)))


# --- registration ---------------------------------------------------------------------------


def test_the_protocol_claims_the_services_the_legacy_table_claimed():
    proto, _ = protocol()
    assert proto.name == "uds"
    assert proto.service_ids == frozenset({0x10, 0x11, 0x19})


# --- 0x10 DiagnosticSessionControl ------------------------------------------------------------


@pytest.mark.parametrize("session", ["01", "02", "03", "04"])
def test_0x10_answers_with_the_fixed_session_parameter_record(session):
    # DEV-17, preserved: P2 = 0x001E, P2* = 0x0BB8, and no session state is kept.
    assert ask("10" + session).hex() == "50" + session + "001e0bb8"


@pytest.mark.parametrize(
    "request_hex, expected", [("1005", "7f1012"), ("1000", "7f1012"), ("10", "7f1013"), ("100100", "7f1013")]
)
def test_0x10_negative_responses(request_hex, expected):
    assert ask(request_hex).hex() == expected


def test_0x10_suppress_positive_response_bit_is_still_not_masked():
    # DEV-07, preserved: out of Phase 6 scope.
    assert ask("1081").hex() == "7f1012"


# --- 0x11 ECUReset -----------------------------------------------------------------------------


@pytest.mark.parametrize("reset", ["01", "02", "03", "05"])
def test_0x11_echoes_the_reset_type(reset):
    assert ask("11" + reset).hex() == "51" + reset


def test_0x11_rapid_power_shutdown_adds_the_power_down_time():
    assert ask("1104").hex() == "51040f"


@pytest.mark.parametrize(
    "request_hex, expected", [("1106", "7f1112"), ("1100", "7f1112"), ("11", "7f1113"), ("110100", "7f1113")]
)
def test_0x11_negative_responses(request_hex, expected):
    assert ask(request_hex).hex() == expected


def test_0x11_suppress_positive_response_bit_is_still_not_masked():
    # DEV-07, preserved.
    assert ask("1181").hex() == "7f1112"


# --- 0x19 ReadDTCInformation --------------------------------------------------------------------


def test_0x19_02_answers_the_configured_codes_with_the_fixed_status():
    # DEV-05 and DEV-16, both preserved at this step: the two-byte form is accepted, the
    # availability mask is FF and every record carries third byte 01 and status 2F.
    assert ask("1902").hex() == "5902ff" + "9477012f" + "0001012f"


def test_0x19_02_with_a_status_mask_is_still_rejected():
    # DEV-05, preserved at this step.
    assert ask("1902ff").hex() == "7f1913"
    assert ask("190200").hex() == "7f1913"


@pytest.mark.parametrize(
    "request_hex, expected", [("1901", "7f1912"), ("190a", "7f1912"), ("1900", "7f1912"), ("19", "7f1913")]
)
def test_0x19_negative_responses(request_hex, expected):
    assert ask(request_hex).hex() == expected


def test_0x19_02_with_no_configured_codes_answers_the_header_alone():
    proto, _ = protocol(())
    assert proto.handle(ServiceRequest(b"\x19\x02")).hex() == "5902ff"


def test_0x19_02_reads_through_the_registry_so_it_follows_the_store():
    proto, store = protocol()
    assert proto.handle(ServiceRequest(b"\x19\x02")).hex() == "5902ff" + "9477012f" + "0001012f"
    store.clear()
    # The status byte is still fixed at this step, so clearing does not yet remove a
    # record; it is the store that is read, not a copy taken at construction.
    assert proto.handle(ServiceRequest(b"\x19\x02")).hex() == "5902ff" + "9477012f" + "0001012f"


# --- unsupported and malformed -------------------------------------------------------------------


@pytest.mark.parametrize("request_hex", ["22f190", "3e00", "14ffffff", "2701", "3101"])
def test_services_this_protocol_does_not_claim_are_not_answered(request_hex):
    # The ECU's route policy answers these, not the protocol; it must not claim them.
    proto, _ = protocol()
    assert bytes.fromhex(request_hex)[0] not in proto.service_ids
