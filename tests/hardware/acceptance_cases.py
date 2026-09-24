"""The ELM327 acceptance cases, written once and run against either backend.

Each case is an :class:`~tests.hardware.tester.AcceptanceCase` over the
:class:`~tests.hardware.tester.DiagnosticTester` protocol, so the same assertions run
against the fake dongle bridged to ``vcan0`` in CI and against a real adapter on the bench.
Writing them twice would let the two drift, and the bench would then prove something subtly
different from what CI proves.

**No case asserts a value the vcan integration suite does not already assert.** That is
[0007 §7.1 rule 3](../../docs/decisions/0007-phase-8-hardware-validation.md): the job of
this suite is to show the same bytes survive a different tester, and a new expectation
belongs in the vcan suite first. Every expected byte string below appears in
``tests/integration/``.

Coverage against [0007 §6.2](../../docs/decisions/0007-phase-8-hardware-validation.md),
stated rather than implied:

===== ========================================= ==========================================
item   subject                                   status here
===== ========================================= ==========================================
1      ``AT Z`` / ``AT I`` / ``AT @1``            covered as a shape check; the exact
                                                 strings are device-specific and belong in
                                                 the bench record, not in an assertion
2      ``AT RV`` supply voltage                   physical only -- a fake has no supply
3      ``AT SP 6`` then ``AT DPN``                covered
4      ``AT SP 0`` automatic search               physical only -- the fake implements no
                                                 search, so passing would prove nothing
5      supported-PID chain                        covered
6      individual Mode 01 parameters              covered
7      multi-parameter request, request order      covered
8      six parameters, multi-frame                covered
9      VIN, multi-frame with item count           covered
10     Mode 03 trouble codes                      covered
11     Mode 04 clear, then Mode 03                covered, mutating
12     UDS on the physical address                covered
13     ``3E 80`` suppressed -> tester's NO DATA    covered
14     ``10 05`` negative response                covered
15     unclaimed service -> ``7F xx 11``          covered
16     cross-protocol clear                       covered, mutating
17     scenario profile over real wire            **not covered here.** It needs the
                                                 simulator started on a different profile,
                                                 which is a property of the run rather than
                                                 of the tester. Phase 8b procedure
18     SIGINT shutdown with a tester connected    **not covered here.** It is about the
                                                 simulator process, and
                                                 ``tests/integration/test_lifecycle.py``
                                                 already pins it
19     independent ``candump`` capture            not a test; a Phase 8b procedure
===== ========================================= ==========================================
"""

from __future__ import annotations

from tests.hardware.elm327_parser import Elm327Error
from tests.hardware.tester import PHYSICAL, AcceptanceCase, DiagnosticTester

# Addresses from the shipped ice_default.yaml profile.
FUNCTIONAL = "7DF"  # OBD broadcast; responses arrive from 0x7E8
OBD_PHYSICAL = "7E0"
UDS_PHYSICAL = "7E1"


def _hex(payload: str) -> bytes:
    return bytes.fromhex(payload)


# --- bring-up ------------------------------------------------------------------------------


def _identifies_itself(tester: DiagnosticTester) -> None:
    identity = tester.at("AT I")
    assert identity, "AT I returned nothing"
    assert "ELM327" in identity.upper(), identity
    description = tester.at("AT @1")
    assert description, "AT @1 returned nothing"


def _reports_a_plausible_supply_voltage(tester: DiagnosticTester) -> None:
    reading = tester.at("AT RV")
    assert reading.upper().endswith("V"), reading
    volts = float(reading.upper().rstrip("V"))
    assert 6.0 <= volts <= 30.0, f"implausible supply voltage {reading!r}"


def _selects_protocol_six(tester: DiagnosticTester) -> None:
    tester.at("AT SP 6")
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("01 00")  # a request must have happened for AT DPN to be meaningful
    assert tester.at("AT DPN").upper().lstrip("A") == "6"


def _automatic_search_settles_on_protocol_six(tester: DiagnosticTester) -> None:
    tester.at("AT SP 0")
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("01 00") == _hex("41001E3F8013")
    assert tester.at("AT DPN").upper().lstrip("A") == "6"


# --- OBD on the functional address ----------------------------------------------------------


def _supported_pid_chain(tester: DiagnosticTester) -> None:
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("01 00") == _hex("41001E3F8013")
    assert tester.ask("01 20") == _hex("412000020001")
    last = tester.ask("01 40")
    assert last == _hex("414044008000")
    assert last[-1] & 0x01 == 0, "the last populated range must not claim a successor"


def _individual_parameters(tester: DiagnosticTester) -> None:
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("01 0C") == _hex("410C0C80"), "engine rpm, 800"
    assert tester.ask("01 0D") == _hex("410D00"), "vehicle speed, 0"
    assert tester.ask("01 05") == _hex("410582"), "coolant, 90 C"
    assert tester.ask("01 2F") == _hex("412F7F"), "fuel level"
    assert tester.ask("01 0B") == _hex("410B21"), "manifold pressure"


def _multi_parameter_request_answers_in_request_order(tester: DiagnosticTester) -> None:
    # DEV-18, corrected in Phase 5.1.
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("01 05 0C 2F 51") == _hex("410582" "0C0C80" "2F7F" "5101")


def _six_parameters_come_back_multi_frame(tester: DiagnosticTester) -> None:
    # Longer than one CAN frame, so the kernel segments it and the tester's own ISO-TP
    # stack sends the flow control. That is the part no vcan test exercises with a
    # third-party implementation.
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("01 04 05 0B 0C 0D 0E") == _hex(
        "4104" "38" "05" "82" "0B" "21" "0C" "0C80" "0D" "00" "0E" "94"
    )


def _vin_comes_back_multi_frame(tester: DiagnosticTester) -> None:
    # DEV-02: the third byte is the number of data items, one VIN.
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("09 02") == b"\x49\x02\x01TESTVIN0123456789"


def _trouble_codes_are_reported(tester: DiagnosticTester) -> None:
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("03") == _hex("430294770001"), "B1477 and P0001"


def _clearing_over_obd_empties_the_store(tester: DiagnosticTester) -> None:
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("03") == _hex("430294770001")
    assert tester.ask("04") == _hex("44")
    assert tester.ask("03") == _hex("4300")


# --- UDS on the physical address -------------------------------------------------------------


def _uds_services_on_the_physical_address(tester: DiagnosticTester) -> None:
    tester.at("AT SH " + UDS_PHYSICAL)
    assert tester.ask("10 01") == _hex("5001001E0BB8"), "DiagnosticSessionControl"
    assert tester.ask("11 01") == _hex("5101"), "ECUReset"
    assert tester.ask("19 02 FF") == _hex("59028C" "9477010C" "0001010C"), "ReadDTCInformation"
    assert tester.ask("3E 00") == _hex("7E00"), "TesterPresent"


def _a_suppressed_positive_response_reaches_the_tester_as_no_data(tester: DiagnosticTester) -> None:
    # 0007 section 6.2 item 13, and the most interesting case in the list: Phase 7's
    # suppression is invisible to a raw socket except as a timeout, but a real tester
    # reports its own no-data condition. DEV-07.
    tester.at("AT SH " + UDS_PHYSICAL)
    assert tester.ask("3E 00") == _hex("7E00")
    try:
        answer = tester.ask("3E 80")
    except Elm327Error as error:
        assert error.message == "NO DATA", f"expected NO DATA, got {error.message!r}"
    else:
        raise AssertionError(f"expected silence for 3E 80, tester returned {answer.hex()}")
    # The channel still works: the silence was a choice, not a dead bus.
    assert tester.ask("3E 00") == _hex("7E00")


def _a_negative_response_reaches_the_tester_unaltered(tester: DiagnosticTester) -> None:
    tester.at("AT SH " + UDS_PHYSICAL)
    assert tester.ask("10 05") == _hex("7F1012")


def _an_unclaimed_service_is_refused_with_nrc_11(tester: DiagnosticTester) -> None:
    # DEV-06, from the route's unsupported-service policy.
    tester.at("AT SH " + UDS_PHYSICAL)
    assert tester.ask("22 F1 90") == _hex("7F2211")


def _clearing_over_uds_is_visible_to_obd(tester: DiagnosticTester) -> None:
    # Plan rule 7: 0x14 and Mode 04 clear one shared store, so a clear on the UDS channel
    # changes what the OBD channel reports.
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("03") == _hex("430294770001")
    tester.at("AT SH " + UDS_PHYSICAL)
    assert tester.ask("14 FF FF FF") == _hex("54")
    assert tester.ask("19 02 FF") == _hex("59028C")
    tester.at("AT SH " + FUNCTIONAL)
    assert tester.ask("03") == _hex("4300")


# --- the registry ------------------------------------------------------------------------------

ACCEPTANCE_CASES: tuple[AcceptanceCase, ...] = (
    AcceptanceCase("identifies-itself", "0007 6.2 item 1", _identifies_itself),
    AcceptanceCase(
        "supply-voltage", "0007 6.2 item 2", _reports_a_plausible_supply_voltage,
        applies_to=frozenset({PHYSICAL}),
    ),
    AcceptanceCase("protocol-6-selected", "0007 6.2 item 3", _selects_protocol_six),
    AcceptanceCase(
        "auto-search-finds-protocol-6", "0007 6.2 item 4", _automatic_search_settles_on_protocol_six,
        applies_to=frozenset({PHYSICAL}),
    ),
    AcceptanceCase("supported-pid-chain", "0007 6.2 item 5", _supported_pid_chain),
    AcceptanceCase("individual-parameters", "0007 6.2 item 6", _individual_parameters),
    AcceptanceCase("multi-parameter-order", "0007 6.2 item 7", _multi_parameter_request_answers_in_request_order),
    AcceptanceCase("six-parameters-multi-frame", "0007 6.2 item 8", _six_parameters_come_back_multi_frame),
    AcceptanceCase("vin-multi-frame", "0007 6.2 item 9", _vin_comes_back_multi_frame),
    AcceptanceCase("trouble-codes", "0007 6.2 item 10", _trouble_codes_are_reported),
    AcceptanceCase("uds-physical-services", "0007 6.2 item 12", _uds_services_on_the_physical_address),
    AcceptanceCase("suppressed-response-is-no-data", "0007 6.2 item 13", _a_suppressed_positive_response_reaches_the_tester_as_no_data),
    AcceptanceCase("negative-response", "0007 6.2 item 14", _a_negative_response_reaches_the_tester_unaltered),
    AcceptanceCase("unclaimed-service-nrc-11", "0007 6.2 item 15", _an_unclaimed_service_is_refused_with_nrc_11),
    # Mutating cases last: each clears the shared DTC store, so anything reading DTCs must
    # run before them. Each backend restores a known state afterwards.
    AcceptanceCase("clear-over-obd", "0007 6.2 item 11", _clearing_over_obd_empties_the_store, mutates=True),
    AcceptanceCase("clear-over-uds-seen-by-obd", "0007 6.2 item 16", _clearing_over_uds_is_visible_to_obd, mutates=True),
)


def cases_for(backend: str) -> tuple[AcceptanceCase, ...]:
    """The cases that are meaningful against ``backend``."""
    return tuple(case for case in ACCEPTANCE_CASES if case.applies(backend))
