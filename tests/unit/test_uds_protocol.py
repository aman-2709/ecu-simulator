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
    return UdsProtocol(dtc_providers=providers, dtcs=store), store


def ask(hex_request):
    proto, _ = protocol()
    return proto.handle(ServiceRequest(bytes.fromhex(hex_request)))


# --- registration ---------------------------------------------------------------------------


def test_the_protocol_claims_the_services_the_legacy_table_claimed():
    proto, _ = protocol()
    assert proto.name == "uds"
    assert proto.service_ids == frozenset({0x10, 0x11, 0x14, 0x19})


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


def test_0x19_02_answers_the_matching_codes_with_their_derived_status():
    # B1477 is pending and confirmed (0x0C); P0001 is pending only (0x04). The third byte
    # of each number stays the frozen 0x01 (DEV-16).
    assert ask("1902ff").hex() == "59028c" + "9477010c" + "00010104"


def test_0x19_02_requires_a_status_mask():
    # DEV-05: the mask is a mandatory request parameter.
    assert ask("1902").hex() == "7f1913"
    assert ask("1902ff00").hex() == "7f1913"


@pytest.mark.parametrize("mask", ["00", "40", "10", "01", "02", "20", "80"])
def test_0x19_02_omits_every_record_a_mask_matches_no_bit_of(mask):
    # (status & mask) != 0 is the filter. Neither code sets any of these bits, and five of
    # them are outside the advertised availability mask altogether.
    assert ask("1902" + mask).hex() == "59028c"


def test_0x19_02_filtering_on_confirmed_keeps_only_the_confirmed_code():
    assert ask("190208").hex() == "59028c" + "9477010c"


def test_0x19_02_filtering_on_pending_keeps_both():
    assert ask("190204").hex() == "59028c" + "9477010c" + "00010104"


@pytest.mark.parametrize(
    "request_hex, expected", [("1901", "7f1912"), ("190a", "7f1912"), ("1900", "7f1912"), ("19", "7f1913")]
)
def test_0x19_negative_responses(request_hex, expected):
    assert ask(request_hex).hex() == expected


@pytest.mark.parametrize("request_hex", ["1901", "1901ff", "190a", "190aff", "1982", "1982ff", "19ff00000000"])
def test_0x19_an_unsupported_subfunction_is_rejected_as_one_at_any_length(request_hex):
    assert ask(request_hex).hex() == "7f1912"


def test_0x19_without_a_subfunction_is_a_length_error():
    assert ask("19").hex() == "7f1913"


def test_0x19_02_with_no_configured_codes_answers_the_header_alone():
    proto, _ = protocol(())
    assert proto.handle(ServiceRequest(b"\x19\x02\xff")).hex() == "59028c"


def test_0x19_02_reads_through_the_registry_so_it_follows_the_store():
    proto, store = protocol()
    assert proto.handle(ServiceRequest(b"\x19\x02\xff")).hex() == "59028c" + "9477010c" + "00010104"
    store.clear()
    # Every flag is off, so every status byte is zero and no mask matches anything.
    assert proto.handle(ServiceRequest(b"\x19\x02\xff")).hex() == "59028c"


# --- 0x14 ClearDiagnosticInformation ---------------------------------------------------------


def test_0x14_for_all_dtcs_is_acknowledged_with_a_bare_service_byte():
    assert ask("14ffffff") == b"\x54"


def test_0x14_clears_the_shared_store():
    proto, store = protocol()
    assert proto.handle(ServiceRequest(b"\x14\xff\xff\xff")) == b"\x54"
    assert store.confirmed == () and store.pending == () and store.indicator_on is False


def test_0x14_keeps_the_configured_codes():
    proto, store = protocol()
    proto.handle(ServiceRequest(b"\x14\xff\xff\xff"))
    assert store.codes == ("B1477", "P0001")


def test_0x19_reports_nothing_after_0x14():
    proto, _ = protocol()
    assert proto.handle(ServiceRequest(b"\x19\x02\xff")).hex() == "59028c" + "9477010c" + "00010104"
    proto.handle(ServiceRequest(b"\x14\xff\xff\xff"))
    assert proto.handle(ServiceRequest(b"\x19\x02\xff")).hex() == "59028c"


@pytest.mark.parametrize("group", ["000000", "ffff33", "123456", "fffffe"])
def test_0x14_for_any_other_group_is_out_of_range_and_clears_nothing(group):
    proto, store = protocol()
    assert proto.handle(ServiceRequest(bytes.fromhex("14" + group))).hex() == "7f1431"
    assert tuple(s.code for s in store.confirmed) == ("B1477",)


@pytest.mark.parametrize("request_hex", ["14", "14ff", "14ffff", "14ffffffff", "14ffffff00"])
def test_0x14_with_the_wrong_length_is_a_length_error(request_hex):
    # The five-byte MemorySelection form of ISO 14229-1:2020 and later is refused rather
    # than half-implemented: this simulator has one fault memory.
    assert ask(request_hex).hex() == "7f1413"


def test_0x14_on_an_empty_store_still_acknowledges():
    proto, _ = protocol(())
    assert proto.handle(ServiceRequest(b"\x14\xff\xff\xff")) == b"\x54"


# --- unsupported and malformed -------------------------------------------------------------------


@pytest.mark.parametrize("request_hex", ["22f190", "3e00", "2701", "3101"])
def test_services_this_protocol_does_not_claim_are_not_answered(request_hex):
    # The ECU's route policy answers these, not the protocol; it must not claim them.
    proto, _ = protocol()
    assert bytes.fromhex(request_hex)[0] not in proto.service_ids
