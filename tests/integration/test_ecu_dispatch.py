"""Which services each address answers, on the real kernel ISO-TP path.

The engine ECU serves the protocols its route enables. Both physical ids (0x7E0, 0x7E1)
enable OBD and UDS, so each answers either protocol and answers an unknown service with
NRC 0x11 (DEV-06). The OBD broadcast id 0x7DF enables OBD only, so a UDS request there
reaches no protocol at all and nothing is transmitted; an unknown service is silent there
too, which is the OBD convention for a broadcast.
"""

import pytest

from tests.integration.conftest import FunctionalTester, Simulator, assert_silent, open_tester_socket

SESSION_RESPONSE = bytes.fromhex("5001001e0bb8")
FUEL_RESPONSE = b"\x41\x2f\x7f"

# The universal probe for assert_silent. Every route in the shipped profile answers it --
# 0x7DF, 0x7E0 and 0x7E1 -- which the three "is_answered" tests below already pin.
OBD_PROBE = b"\x01\x2f"


@pytest.fixture
def functional(vcan, simulator: Simulator):
    tester = FunctionalTester(vcan, functional_id=0x7DF, response_id=0x7E8, physical_id=0x7E0)
    try:
        yield tester
    finally:
        tester.close()


@pytest.fixture
def obd_physical(vcan, simulator: Simulator):
    sock = open_tester_socket(vcan, rx_id=0x7E8, tx_id=0x7E0)
    try:
        yield sock
    finally:
        sock.close()


@pytest.fixture
def uds_physical(vcan, simulator: Simulator):
    sock = open_tester_socket(vcan, rx_id=0x7E9, tx_id=0x7E1)
    try:
        yield sock
    finally:
        sock.close()


# --- the OBD broadcast route enables OBD only --------------------------------------------------


def test_obd_service_on_the_functional_address_is_answered(functional):
    functional.send(b"\x01\x2f")
    assert functional.recv() == FUEL_RESPONSE


@pytest.mark.parametrize("request_hex", ["1001", "1005", "1901"], ids=["session", "bad-subfunction", "read-dtc"])
def test_uds_service_on_the_functional_address_reaches_no_protocol(functional, request_hex):
    # UDS is not enabled on 0x7DF, so nothing is produced: neither the positive response
    # for a well-formed request nor a negative one for a malformed request.
    assert_silent(functional, bytes.fromhex(request_hex), OBD_PROBE, FUEL_RESPONSE)


def test_the_functional_channel_still_serves_obd_after_an_ignored_uds_request(functional):
    functional.send(b"\x10\x01")
    with pytest.raises(TimeoutError):
        functional.recv()
    functional.send(b"\x01\x51")
    assert functional.recv() == b"\x41\x51\x01"


def test_unknown_service_on_the_functional_address_gets_no_response(functional):
    assert_silent(functional, b"\x22\xf1\x90", OBD_PROBE, FUEL_RESPONSE)


# --- both physical routes enable OBD and UDS ----------------------------------------------------


def test_uds_service_on_the_obd_physical_address_is_answered(obd_physical):
    obd_physical.send(b"\x10\x01")
    assert obd_physical.recv() == SESSION_RESPONSE


def test_obd_service_on_the_uds_physical_address_is_answered(uds_physical):
    uds_physical.send(b"\x01\x2f")
    assert uds_physical.recv() == FUEL_RESPONSE


def test_a_negative_response_from_an_enabled_protocol_is_transmitted(uds_physical):
    # UDS is enabled here, so its negative response goes out unaltered; nothing filters
    # a response for being negative.
    uds_physical.send(b"\x10\x05")
    assert uds_physical.recv() == b"\x7f\x10\x12"


def test_unknown_service_on_a_physical_address_gets_nrc_0x11(uds_physical):
    # DEV-06 corrected, from the route's unsupported-service policy.
    uds_physical.send(b"\x22\xf1\x90")
    assert uds_physical.recv() == b"\x7f\x22\x11"


def test_tester_present_is_answered_on_the_wire(uds_physical):
    # DEV-23, second half, Phase 7. Until this phase 0x3E was answered 7F 3E 11 by the
    # same unsupported-service policy as the request above.
    uds_physical.send(b"\x3e\x00")
    assert uds_physical.recv() == b"\x7e\x00"


def test_tester_present_changes_nothing_a_later_request_can_see(uds_physical):
    # Stateless on the wire, not just in the handler: the service is asked several times
    # between two reads of the same data and the second read is byte-identical.
    uds_physical.send(b"\x19\x02\xff")
    before = uds_physical.recv()
    for _ in range(3):
        uds_physical.send(b"\x3e\x00")
        assert uds_physical.recv() == b"\x7e\x00"
    uds_physical.send(b"\x19\x02\xff")
    assert uds_physical.recv() == before


def test_a_tester_present_subfunction_this_server_does_not_support_is_refused(uds_physical):
    uds_physical.send(b"\x3e\x01")
    assert uds_physical.recv() == b"\x7f\x3e\x12"


def test_a_suppressed_positive_response_is_not_transmitted(uds_physical):
    # DEV-07, Phase 7, on the wire rather than in the handler: nothing at all is put on
    # the bus, so a tester that asked for silence waits for its own timeout.
    assert_silent(uds_physical, b"\x3e\x80", OBD_PROBE, FUEL_RESPONSE)


def test_the_channel_still_works_after_a_suppressed_response(uds_physical):
    # The suppression withholds one response; it does not wedge the socket or leave a
    # half-sent frame behind for the next request to trip over.
    uds_physical.send(b"\x3e\x80")
    with pytest.raises(TimeoutError):
        uds_physical.recv()
    uds_physical.send(b"\x3e\x00")
    assert uds_physical.recv() == b"\x7e\x00"


@pytest.mark.parametrize("request_hex", ["1083", "1181"], ids=["session", "reset"])
def test_a_suppressed_service_that_is_not_tester_present_is_silent_too(uds_physical, request_hex):
    # The rule is generic, and the wire proves it for the two services DEV-07 named.
    assert_silent(uds_physical, bytes.fromhex(request_hex), OBD_PROBE, FUEL_RESPONSE)


def test_a_suppressed_request_does_not_hide_a_negative_response(uds_physical):
    # 0x85 masks to reset type 0x05, which is supported, so it is silent; 0x86 masks to
    # 0x06, which is not, so its refusal goes out.
    uds_physical.send(b"\x11\x85")
    with pytest.raises(TimeoutError):
        uds_physical.recv()
    uds_physical.send(b"\x11\x86")
    assert uds_physical.recv() == b"\x7f\x11\x12"


def test_reading_dtcs_can_be_asked_for_silently(uds_physical):
    # 0x19 has a sub-function, so it takes part in the same rule. This is a deliberate
    # change to what Phase 6 put on the wire for 19 82 FF, which was 7F 19 12.
    uds_physical.send(b"\x19\x02\xff")
    assert uds_physical.recv() == bytes.fromhex("59028c9477010c0001010c")
    # The probe goes after the silence: the positive read above proves the channel was
    # alive then, not that it is alive now.
    assert_silent(uds_physical, b"\x19\x82\xff", OBD_PROBE, FUEL_RESPONSE)


def test_a_suppressed_read_without_a_status_mask_still_reports_its_length_error(uds_physical):
    uds_physical.send(b"\x19\x82")
    assert uds_physical.recv() == b"\x7f\x19\x13"


def test_clearing_dtcs_is_not_mistaken_for_a_suppressed_request(mutating, uds_physical):
    # 0x14 has no sub-function, and the second byte of its only served groupOfDTC is 0xFF.
    # Nothing masks it and nothing withholds the acknowledgement.
    #
    # This clears the shared store, so it takes `mutating` and the simulator is replaced
    # afterwards. Without it the test passed only because of where it sits in the file:
    # every test that reads DTCs happens to run before it. A bench run is partly manual and
    # partly out of order, which is exactly the condition that exposes the dependency.
    uds_physical.send(b"\x14\xff\xff\xff")
    assert uds_physical.recv() == b"\x54"


def test_tester_present_on_the_obd_broadcast_address_reaches_no_protocol(functional):
    # UDS is not enabled on 0x7DF, so claiming 0x3E does not make it answerable there.
    assert_silent(functional, b"\x3e\x00", OBD_PROBE, FUEL_RESPONSE)
