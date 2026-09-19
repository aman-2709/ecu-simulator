"""Which services each address answers, on the real kernel ISO-TP path.

The engine ECU serves the protocols its route enables. Both physical ids (0x7E0, 0x7E1)
enable OBD and UDS, so each answers either protocol and answers an unknown service with
NRC 0x11 (DEV-06). The OBD broadcast id 0x7DF enables OBD only, so a UDS request there
reaches no protocol at all and nothing is transmitted; an unknown service is silent there
too, which is the OBD convention for a broadcast.
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


# --- the OBD broadcast route enables OBD only --------------------------------------------------


def test_obd_service_on_the_functional_address_is_answered(functional):
    functional.send(b"\x01\x2f")
    assert functional.recv() == FUEL_RESPONSE


@pytest.mark.parametrize("request_hex", ["1001", "1005", "1901"], ids=["session", "bad-subfunction", "read-dtc"])
def test_uds_service_on_the_functional_address_reaches_no_protocol(functional, request_hex):
    # UDS is not enabled on 0x7DF, so nothing is produced: neither the positive response
    # for a well-formed request nor a negative one for a malformed request.
    functional.send(bytes.fromhex(request_hex))
    with pytest.raises(TimeoutError):
        functional.recv()


def test_the_functional_channel_still_serves_obd_after_an_ignored_uds_request(functional):
    functional.send(b"\x10\x01")
    with pytest.raises(TimeoutError):
        functional.recv()
    functional.send(b"\x01\x51")
    assert functional.recv() == b"\x41\x51\x01"


def test_unknown_service_on_the_functional_address_gets_no_response(functional):
    functional.send(b"\x22\xf1\x90")
    with pytest.raises(TimeoutError):
        functional.recv()


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
