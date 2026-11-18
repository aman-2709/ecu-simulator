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
from ecu_simulator.protocols.uds.protocol import READ_DTC_INFORMATION

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
    # that phase; Phase 11 added 0x22. Nothing the legacy table claimed was dropped.
    proto, _ = protocol()
    assert proto.service_ids == frozenset({0x10, 0x11, 0x14, 0x19, 0x22, 0x3E})


# --- 0x10 DiagnosticSessionControl ------------------------------------------------------------


@pytest.mark.parametrize("session", ["01", "02", "03", "04"])
def test_0x10_answers_with_the_fixed_session_parameter_record(session):
    # DEV-17: P2 = 0x001E, P2* = 0x0BB8, the default record Phase 11 kept (ruling Q3).
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


@pytest.mark.parametrize("request_hex", ["1901", "1901ff", "190a", "190aff", "19ff00000000"])
def test_0x19_an_unsupported_subfunction_is_rejected_as_one_at_any_length(request_hex):
    # 1982 and 1982ff left this list in Phase 7: 0x82 is report type 0x02 with the
    # suppressPosRspMsgIndicationBit, not an unsupported report type. Both are asserted
    # in their own tests below. 19 FF 00 00 00 00 stays: 0xFF masks to 0x7F, which this
    # server does not support either.
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


@pytest.mark.parametrize("request_hex", ["2801", "2701", "3101"])
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
    # No counter: the answer never depends on how often it was asked. Since Phase 11 each
    # request restarts S3, which no answer to 0x3E reveals.
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

    assert SUB_FUNCTION_SERVICES == frozenset({0x10, 0x11, 0x19, 0x3E})


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


# --- the suppress bit reaches 0x19 too -----------------------------------------------------------
#
# 0x19 ReadDTCInformation has a sub-function -- the report type -- so the rule above
# applies to it exactly as it applies to 0x10, 0x11 and 0x3E. Phase 6 answered 19 82 FF
# with 7F 19 12, treating 0x82 as a report type it does not support; that predates the
# generic handling and this phase corrects it. The alternative was to declare 0x19 as
# having no sub-function, which would have kept Phase 6's bytes by writing something into
# the table that is not true of the service.


def test_0x19_is_declared_as_a_subfunction_service():
    from ecu_simulator.protocols.uds.protocol import SERVICE_IDS, SUB_FUNCTION_SERVICES

    assert READ_DTC_INFORMATION in SUB_FUNCTION_SERVICES
    # 0x22 joined 0x14 in Phase 11: ReadDataByIdentifier has no sub-function, and a DID's
    # high byte, such as the F1 of F190, has bit 7 set exactly where the bit would be.
    assert SERVICE_IDS - SUB_FUNCTION_SERVICES == frozenset({0x14, 0x22})


def test_0x19_02_is_unchanged_by_any_of_this():
    assert ask("1902ff").hex() == "59028c" + "9477010c" + "00010104"


def test_0x19_82_runs_the_same_read_and_withholds_its_positive_response():
    assert ask("1982ff") is None


def test_0x19_82_selects_report_type_0x02_and_then_its_own_length_rule():
    # The proof that 0x82 is masked to 0x02 and dispatched as 0x02, rather than the
    # response being dropped: 0x02 requires a status mask, so the two-byte form fails
    # 0x02's length rule and the four-byte form does too. A negative response is never
    # withheld, so both are visible.
    assert ask("1982").hex() == "7f1913"
    assert ask("1982ff00").hex() == "7f1913"


@pytest.mark.parametrize("report_type", ["81", "8a", "80", "ff"])
def test_0x19_with_the_suppress_bit_on_a_report_type_this_server_lacks_is_still_refused(report_type):
    # 0x81, 0x8A, 0x80 and 0xFF mask to 0x01, 0x0A, 0x00 and 0x7F, none of which this
    # server supports. Each keeps the 0x12 it has always had.
    assert ask("19" + report_type).hex() == "7f1912"


@pytest.mark.parametrize("report_type", ["01", "0a", "00", "7f"])
def test_0x19_without_the_suppress_bit_refuses_the_same_report_types(report_type):
    # The same four values with bit 7 clear, answered identically. The bit changes
    # whether a positive response is sent; it never changes which sub-function was asked
    # for or what a refusal says.
    assert ask("19" + report_type).hex() == "7f1912"


def test_the_status_mask_is_not_masked_on_its_way_to_the_service():
    # Only the sub-function byte has bit 7 removed. The status mask is 0x19/0x02's own
    # parameter and keeps all eight bits: 0x80 is the "indicator requested" bit, and a
    # request filtering on it must still match a code that has it set.
    proto, _ = protocol((DtcState("B1477", indicator_requested=True),))
    assert proto.handle(ServiceRequest(b"\x19\x02\x80")).hex() == "59028c" + "94770180"
    assert proto.handle(ServiceRequest(b"\x19\x82\x80")) is None


def test_0x19_without_a_subfunction_is_still_a_length_error():
    assert ask("19").hex() == "7f1913"


# --- session state machine (Phase 11) ------------------------------------------------------------


def session_protocol(timings=None, **kwargs):
    from ecu_simulator.protocols.uds import UdsSession

    session = UdsSession(timings) if timings is not None else UdsSession()
    store = DtcStore(DEFAULT)
    providers = DtcRegistry()
    providers.register(DtcStoreProvider("engine", store))
    return UdsProtocol(dtc_providers=providers, dtcs=store, session=session, **kwargs)


def send(proto, hex_request):
    response = proto.handle(ServiceRequest(bytes.fromhex(hex_request)))
    return response.hex() if response is not None else None


def test_the_ecu_starts_in_the_default_session():
    assert session_protocol().session.active == 0x01


@pytest.mark.parametrize("session", [0x02, 0x03, 0x04, 0x01])
def test_0x10_makes_the_requested_session_active(session):
    proto = session_protocol()
    assert send(proto, f"10{session:02x}") == f"50{session:02x}001e0bb8"
    assert proto.session.active == session


def test_0x10_re_entering_the_active_session_is_answered_normally():
    proto = session_protocol()
    send(proto, "1003")
    assert send(proto, "1003") == "5003001e0bb8"
    assert proto.session.active == 0x03


@pytest.mark.parametrize("request_hex", ["1005", "10", "100300"])
def test_a_refused_0x10_changes_no_session(request_hex):
    proto = session_protocol()
    send(proto, "1003")
    assert send(proto, request_hex).startswith("7f10")
    assert proto.session.active == 0x03


def test_0x10_with_the_suppress_bit_changes_the_session_silently():
    proto = session_protocol()
    assert send(proto, "1083") is None
    assert proto.session.active == 0x03


def test_the_session_record_comes_from_the_configured_timing():
    from ecu_simulator.protocols.uds import SessionTiming

    proto = session_protocol({0x01: SessionTiming(30, 30000), 0x03: SessionTiming(50, 5000)})
    # P2 50 ms = 0x0032 in 1 ms units; P2* 5000 ms = 0x01F4 in 10 ms units.
    assert send(proto, "1003") == "5003003201f4"
    assert send(proto, "1001") == "5001001e0bb8"


def test_a_session_left_out_of_the_configuration_is_not_supported():
    from ecu_simulator.protocols.uds import SessionTiming

    proto = session_protocol({0x01: SessionTiming(30, 30000), 0x03: SessionTiming(30, 30000)})
    assert send(proto, "1002") == "7f1012"
    assert proto.session.active == 0x01


@pytest.mark.parametrize(
    "p2, p2_star", [(-1, 0), (0x10000, 0), (0, 5), (0, 655360)], ids=["neg", "p2-wide", "p2star-res", "p2star-wide"]
)
def test_a_timing_the_record_cannot_carry_is_refused(p2, p2_star):
    from ecu_simulator.protocols.uds import SessionTiming

    with pytest.raises(ValueError):
        SessionTiming(p2, p2_star)


def test_a_session_table_without_the_default_session_is_refused():
    from ecu_simulator.protocols.uds import SessionTiming, UdsSession

    with pytest.raises(ValueError, match="default session"):
        UdsSession({0x03: SessionTiming(30, 30000)})


# --- S3 and TesterPresent timing (Phase 11) ------------------------------------------------------


def timed_protocol(s3_ms=5000):
    from ecu_simulator.clock import SimulatedClock
    from ecu_simulator.protocols.uds import UdsSession

    clock = SimulatedClock()
    return UdsProtocol(session=UdsSession(s3_ms=s3_ms, clock=clock)), clock


def test_s3_keeps_the_session_for_exactly_s3_and_ends_it_after():
    proto, clock = timed_protocol()
    send(proto, "1003")
    clock.advance(4.999)
    send(proto, "3e00")
    assert proto.session.active == 0x03, "alive 1 ms before S3"
    clock.advance(5.0)
    send(proto, "3e00")
    assert proto.session.active == 0x03, "alive at exactly S3"
    clock.advance(5.001)
    send(proto, "3e00")
    assert proto.session.active == 0x01, "default 1 ms after S3"


def test_tester_present_keeps_a_session_alive_past_s3():
    proto, clock = timed_protocol()
    send(proto, "1003")
    for _ in range(5):
        clock.advance(4.0)
        assert send(proto, "3e00") == "7e00"
    assert proto.session.active == 0x03


def test_a_suppressed_tester_present_keeps_the_session_alive_too():
    proto, clock = timed_protocol()
    send(proto, "1003")
    for _ in range(3):
        clock.advance(4.0)
        assert send(proto, "3e80") is None
    assert proto.session.active == 0x03


@pytest.mark.parametrize("request_hex", ["1902ff", "1005", "3e01", "14ffffff", "19"])
def test_any_request_the_protocol_handles_restarts_s3(request_hex):
    # Ruling Q4, [ECUC_Dcm_01240]: S3 runs "while not receiving any diagnostic request
    # message", so positive and negative answers alike keep the session.
    proto, clock = timed_protocol()
    send(proto, "1003")
    clock.advance(4.0)
    send(proto, request_hex)
    clock.advance(4.0)
    send(proto, "3e00")
    assert proto.session.active == 0x03


def test_expiry_happens_before_the_request_is_served():
    # The request that arrives after S3 is served in the default session: here, re-entering
    # the extended session works and starts S3 afresh.
    proto, clock = timed_protocol()
    send(proto, "1003")
    clock.advance(6.0)
    assert send(proto, "1003") == "5003001e0bb8"
    assert proto.session.active == 0x03
    clock.advance(5.0)
    send(proto, "3e00")
    assert proto.session.active == 0x03


def test_s3_is_configurable():
    proto, clock = timed_protocol(s3_ms=8000)
    send(proto, "1003")
    clock.advance(7.9)
    send(proto, "3e00")
    assert proto.session.active == 0x03
    clock.advance(8.1)
    send(proto, "3e00")
    assert proto.session.active == 0x01


def test_the_default_session_never_times_out():
    proto, clock = timed_protocol()
    send(proto, "3e00")
    clock.advance(3600)
    send(proto, "3e00")
    assert proto.session.active == 0x01


# --- 0x11 effects on the session (Phase 11) ------------------------------------------------------


@pytest.mark.parametrize("reset_type", ["01", "02", "03"])
def test_a_reset_that_restarts_the_ecu_returns_it_to_the_default_session(reset_type):
    proto = session_protocol()
    send(proto, "1003")
    assert send(proto, "11" + reset_type) == "51" + reset_type
    assert proto.session.active == 0x01


@pytest.mark.parametrize("reset_type, expected", [("04", "51040f"), ("05", "5105")])
def test_rapid_power_shutdown_changes_no_session(reset_type, expected):
    proto = session_protocol()
    send(proto, "1003")
    assert send(proto, "11" + reset_type) == expected
    assert proto.session.active == 0x03


def test_a_suppressed_reset_returns_to_the_default_session_silently():
    proto = session_protocol()
    send(proto, "1003")
    assert send(proto, "1181") is None
    assert proto.session.active == 0x01


@pytest.mark.parametrize("request_hex", ["1106", "11", "110100"])
def test_a_refused_reset_changes_no_session(request_hex):
    proto = session_protocol()
    send(proto, "1003")
    assert send(proto, request_hex).startswith("7f11")
    assert proto.session.active == 0x03


def test_a_reset_leaves_the_dtc_store_alone():
    proto = session_protocol()
    send(proto, "1101")
    assert send(proto, "1902ff") == "59028c" + "9477010c" + "00010104"


# --- 0x22 ReadDataByIdentifier (Phase 11) --------------------------------------------------------


def did_protocol(data=None):
    from ecu_simulator.protocols.uds import DidRegistry, UdsSession
    from ecu_simulator.protocols.uds.dids import SessionDidProvider, StaticDidProvider

    session = UdsSession()
    dids = DidRegistry()
    dids.register(SessionDidProvider(session))
    dids.register(StaticDidProvider("profile", data if data is not None else {0xF1A0: b"\x01\x02", 0x0100: b"\xff"}))
    return UdsProtocol(session=session, dids=dids)


def test_0x22_f186_reads_the_active_session():
    proto = did_protocol()
    assert send(proto, "22f186") == "62f18601"
    send(proto, "1003")
    assert send(proto, "22f186") == "62f18603"


def test_0x22_reads_a_profile_did_verbatim():
    assert send(did_protocol(), "22f1a0") == "62f1a00102"


def test_0x22_answers_several_dids_in_request_order():
    assert send(did_protocol(), "220100f1a0f186") == "620100ff" + "f1a00102" + "f18601"


def test_0x22_a_did_asked_twice_is_answered_twice():
    assert send(did_protocol(), "22f186f186") == "62f18601f18601"


def test_0x22_an_unserved_did_is_left_out_when_another_is_served():
    # [SWS_Dcm_00438]: only when none is supported is the request refused.
    assert send(did_protocol(), "22f1a0beef") == "62f1a00102"


@pytest.mark.parametrize("request_hex", ["22beef", "22beefcafe"])
def test_0x22_with_no_served_did_is_out_of_range(request_hex):
    assert send(did_protocol(), request_hex) == "7f2231"


@pytest.mark.parametrize("request_hex", ["22", "22f1", "22f186f1"])
def test_0x22_a_request_that_is_not_whole_dids_is_a_length_error(request_hex):
    assert send(did_protocol(), request_hex) == "7f2213"


def test_0x22_the_did_high_byte_is_not_read_as_a_suppress_bit():
    # F186's first byte has bit 7 set; 0x22 has no sub-function, so it is not masked and
    # the response is not withheld.
    assert send(did_protocol(), "22f186") == "62f18601"


def test_0x22_a_response_up_to_4095_bytes_is_sent_and_a_longer_one_is_too_long():
    # 1 + 2 + 4092 = 4095 bytes; one more data byte makes 4096.
    assert len(bytes.fromhex(send(did_protocol({0x0100: bytes(4092)}), "220100"))) == 4095
    assert send(did_protocol({0x0100: bytes(4093)}), "220100") == "7f2214"


def test_0x22_restarts_s3_like_any_request():
    from ecu_simulator.clock import SimulatedClock
    from ecu_simulator.protocols.uds import DidRegistry, UdsSession
    from ecu_simulator.protocols.uds.dids import SessionDidProvider

    clock = SimulatedClock()
    session = UdsSession(clock=clock)
    dids = DidRegistry()
    dids.register(SessionDidProvider(session))
    proto = UdsProtocol(session=session, dids=dids)
    send(proto, "1003")
    for _ in range(3):
        clock.advance(4.0)
        assert send(proto, "22f186") == "62f18603"
    clock.advance(5.5)
    assert send(proto, "22f186") == "62f18601", "S3 passed: the read itself sees the default session"
