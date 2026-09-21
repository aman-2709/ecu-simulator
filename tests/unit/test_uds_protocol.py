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
    assert frozenset({0x10, 0x11, 0x14, 0x19}) <= proto.service_ids


def test_the_protocol_also_claims_tester_present():
    # Phase 7, DEV-23: 0x3E was answered by the route's unsupported-service policy until
    # this phase. Nothing else was added, and nothing the legacy table claimed was dropped.
    proto, _ = protocol()
    assert proto.service_ids == frozenset({0x10, 0x11, 0x14, 0x19, 0x3E})


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


def test_0x10_suppress_positive_response_bit_is_masked_off():
    # DEV-07, fixed in Phase 7: 10 81 used to be "sub-function 0x81, which is not
    # supported". It is now "sub-function 0x01, and do not answer me if it works".
    assert ask("1081") is None


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


def test_0x11_suppress_positive_response_bit_is_masked_off():
    # DEV-07, fixed in Phase 7; 0x81 masks to hardReset, which this server supports.
    assert ask("1181") is None


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


@pytest.mark.parametrize("request_hex", ["22f190", "2701", "3101"])
def test_services_this_protocol_does_not_claim_are_not_answered(request_hex):
    # The ECU's route policy answers these, not the protocol; it must not claim them.
    proto, _ = protocol()
    assert bytes.fromhex(request_hex)[0] not in proto.service_ids


# --- 0x3E TesterPresent -----------------------------------------------------------------------
#
# Phase 7, DEV-23's remaining half. Stateless by construction: no timer is started, read
# or reset, no session state is created or consulted, and nothing here claims ISO 14229
# session compliance. S3 and session timing are Phase 11. Evidence in
# docs/decisions/0006-phase-7-scenario-and-testerpresent.md, rows W1, W3 and W4.


def test_0x3e_zero_subfunction_is_acknowledged():
    assert ask("3e00").hex() == "7e00"


@pytest.mark.parametrize("request_hex", ["3e01", "3e02", "3e7f", "3eff", "3e81"])
def test_0x3e_any_other_subfunction_is_not_supported(request_hex):
    assert ask(request_hex).hex() == "7f3e12"


@pytest.mark.parametrize("request_hex", ["3e", "3e0000", "3e000000"])
def test_0x3e_a_request_that_is_not_two_bytes_is_a_length_error(request_hex):
    assert ask(request_hex).hex() == "7f3e13"


def test_0x3e_checks_the_subfunction_before_the_length_like_0x19():
    # 3E 01 00 is both an unsupported sub-function and the wrong length. The sub-function
    # decides, which is the ordering Phase 6 chose for 0x19; one global length check first
    # would hide an unsupported sub-function behind a length error.
    assert ask("3e0100").hex() == "7f3e12"


def test_0x3e_answers_the_same_bytes_however_often_it_is_asked():
    # Statelessness, asserted rather than described: no counter, no session, no timer.
    proto, _ = protocol()
    answers = [proto.handle(ServiceRequest(b"\x3e\x00")) for _ in range(5)]
    assert answers == [b"\x7e\x00"] * 5


def test_0x3e_does_not_disturb_the_dtc_store():
    proto, store = protocol()
    proto.handle(ServiceRequest(b"\x3e\x00"))
    assert tuple(s.code for s in store.confirmed) == ("B1477",)
    assert tuple(s.code for s in store.pending) == ("B1477", "P0001")


# --- suppressPosRspMsgIndicationBit --------------------------------------------------------------
#
# Phase 7, DEV-07. Bit 7 of a sub-function byte is not a sub-function value: it is a
# framing rule that applies to every service that has a sub-function, handled once before
# any service sees the byte. AUTOSAR states the three parts separately --
# [SWS_Dcm_00200] a positive response is not sent, [SWS_Dcm_00201] the bit is masked off
# the message, [SWS_Dcm_00204] the handling applies only where the service has a
# sub-function -- and makes that last part per-service configuration,
# [ECUC_Dcm_00737] DcmDsdSidTabSubfuncAvail, rather than something inferred from the
# request. This implementation follows that: an explicit table, never a payload shape.
#
# ISO 14229-1:2026 is licensed and unread. Not standards validated.


def test_the_services_declared_to_have_a_subfunction_are_listed_explicitly():
    from ecu_simulator.protocols.uds.protocol import SUB_FUNCTION_SERVICES

    assert SUB_FUNCTION_SERVICES == frozenset({0x10, 0x11, 0x3E})


@pytest.mark.parametrize("session", ["01", "02", "03", "04"])
def test_0x10_with_the_suppress_bit_sends_nothing(session):
    suppressed = f"{0x80 | int(session, 16):02x}"
    assert ask("10" + session).hex() == "50" + session + "001e0bb8"
    assert ask("10" + suppressed) is None


@pytest.mark.parametrize("session", ["00", "05", "7f"])
def test_0x10_with_the_suppress_bit_still_refuses_a_session_it_does_not_support(session):
    # The proof that the bit is masked off and the sub-function then matched, rather than
    # the response being dropped because bit 7 was set: an unsupported session still gets
    # its negative response, and a negative response is never suppressed.
    suppressed = f"{0x80 | int(session, 16):02x}"
    assert ask("10" + session).hex() == "7f1012"
    assert ask("10" + suppressed).hex() == "7f1012"


@pytest.mark.parametrize("reset_type", ["01", "02", "03", "05"])
def test_0x11_with_the_suppress_bit_sends_nothing(reset_type):
    suppressed = f"{0x80 | int(reset_type, 16):02x}"
    assert ask("11" + reset_type).hex() == "51" + reset_type
    assert ask("11" + suppressed) is None


def test_0x11_rapid_power_shutdown_with_the_suppress_bit_sends_nothing():
    assert ask("1104").hex() == "51040f"
    assert ask("1184") is None


@pytest.mark.parametrize("reset_type", ["00", "06", "7f"])
def test_0x11_with_the_suppress_bit_still_refuses_a_reset_it_does_not_support(reset_type):
    suppressed = f"{0x80 | int(reset_type, 16):02x}"
    assert ask("11" + reset_type).hex() == "7f1112"
    assert ask("11" + suppressed).hex() == "7f1112"


def test_0x3e_with_the_suppress_bit_sends_nothing():
    assert ask("3e00").hex() == "7e00"
    assert ask("3e80") is None


def test_0x3e_with_the_suppress_bit_on_an_unsupported_subfunction_still_refuses_it():
    assert ask("3e81").hex() == "7f3e12"


@pytest.mark.parametrize("request_hex", ["1080", "1180", "3e8000"])
def test_a_suppressed_request_that_is_malformed_still_gets_its_length_error(request_hex):
    # 10 80 and 11 80 mask to sub-function 0x00, which neither service supports, so they
    # are 0x12; 3E 80 00 masks to a well-formed sub-function with a trailing byte, so it
    # is 0x13. Either way the negative response goes out.
    assert ask(request_hex) is not None


def test_a_service_without_a_subfunction_is_not_given_this_handling():
    # 0x14's second byte is part of its groupOfDTC parameter, and in the only group this
    # server serves that byte is 0xFF -- bit 7 set. A shape-based implementation would
    # silently swallow every clear; this one asks the table whether 0x14 has a
    # sub-function at all, and the answer is no.
    proto, store = protocol()
    assert proto.handle(ServiceRequest(b"\x14\xff\xff\xff")) == b"\x54"
    assert store.confirmed == ()


def test_the_groupofdtc_is_not_masked_on_its_way_to_the_service():
    # The other half: if bit 7 were masked off 0x14's second byte, groupOfDTC 0xFFFFFF
    # would arrive as 0x7FFFFF and be refused as out of range.
    proto, _ = protocol()
    assert proto.handle(ServiceRequest(b"\x14\xff\xff\xff")) == b"\x54"
