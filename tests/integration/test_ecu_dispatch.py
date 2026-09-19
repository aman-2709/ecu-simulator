"""Which services each address answers, on the real kernel ISO-TP path.

Phase 2 routed by endpoint name: a UDS SID arriving on an OBD address, or an OBD SID on
the UDS address, was dropped. Since Phase 3 every address of the implicit ``engine`` ECU
routes to that one ECU, which dispatches by SID, so each registered service is served on
each of the ECU's addresses, and a SID no protocol serves is answered with NRC 0x11 on a
physical address and ignored on the functional one (DEV-06 corrected).
"""

import pytest

from tests.integration.conftest import FunctionalTester, Simulator, open_tester_socket

SESSION_RESPONSE = bytes.fromhex("5001001e0bb8")
FUEL_RESPONSE = b"\x41\x2f\x7f"


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


# --- UDS SID on the OBD addresses ------------------------------------------------------------


def test_uds_sid_on_obd_physical_address_is_answered(obd_physical):
    obd_physical.send(b"\x10\x01")
    assert obd_physical.recv() == SESSION_RESPONSE


def test_uds_sid_on_functional_address_is_answered_on_the_physical_response_id(functional):
    functional.send(b"\x10\x01")
    assert functional.recv() == SESSION_RESPONSE


# --- OBD SID on the UDS address ---------------------------------------------------------------


def test_obd_sid_on_uds_address_is_answered(uds_physical):
    uds_physical.send(b"\x01\x2f")
    assert uds_physical.recv() == FUEL_RESPONSE


# --- SIDs no protocol serves (DEV-06) --------------------------------------------------------


def test_unsupported_sid_on_uds_address_gets_nrc_0x11(uds_physical):
    # DEV-06 corrected.
    uds_physical.send(b"\x22\xf1\x90")
    assert uds_physical.recv() == b"\x7f\x22\x11"


def test_negative_response_is_not_sent_to_a_functional_request(functional):
    # A UDS sub-function error reaches the functional address only since Phase 3; no
    # negative response goes out there, as was the case before.
    functional.send(b"\x10\x05")
    with pytest.raises(TimeoutError):
        functional.recv()
    # the channel still works
    functional.send(b"\x10\x01")
    assert functional.recv() == SESSION_RESPONSE


def test_unsupported_sid_on_functional_address_gets_no_response(functional):
    # Unchanged by DEV-06: negative responses are not sent to functionally addressed requests.
    functional.send(b"\x22\xf1\x90")
    with pytest.raises(TimeoutError):
        functional.recv()
