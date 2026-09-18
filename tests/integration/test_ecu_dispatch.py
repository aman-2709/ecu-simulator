"""Which services each address answers, on the real kernel ISO-TP path.

Phase 2 routed by endpoint name: a UDS SID arriving on an OBD address, or an OBD SID on
the UDS address, was dropped. Phase 3 routes every address of the implicit ``engine``
ECU to that one ECU, which dispatches by SID, so each registered service is served on
each of the ECU's addresses. Plain tests pin today's behavior; strict xfails carry the
Phase 3 behavior and are flipped by the commit that introduces it. DEV-06 (unsupported
SID on a physical address answered with NRC 0x11) has its own xfail and commit.
"""

import pytest

from tests.characterization.conftest import xfail_deviation
from tests.integration.conftest import FunctionalTester, Simulator, open_tester_socket

SESSION_RESPONSE = bytes.fromhex("5001001e0bb8")
FUEL_RESPONSE = b"\x41\x2f\x7f"

phase3 = pytest.mark.xfail(strict=True, reason="Phase 3: the engine ECU serves every registered SID on each address")


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


def test_uds_sid_on_obd_physical_address_is_dropped_today(obd_physical):
    obd_physical.send(b"\x10\x01")
    with pytest.raises(TimeoutError):
        obd_physical.recv()


@phase3
def test_uds_sid_on_obd_physical_address_is_answered(obd_physical):
    obd_physical.send(b"\x10\x01")
    assert obd_physical.recv() == SESSION_RESPONSE


def test_uds_sid_on_functional_address_is_dropped_today(functional):
    functional.send(b"\x10\x01")
    with pytest.raises(TimeoutError):
        functional.recv()


@phase3
def test_uds_sid_on_functional_address_is_answered_on_the_physical_response_id(functional):
    functional.send(b"\x10\x01")
    assert functional.recv() == SESSION_RESPONSE


# --- OBD SID on the UDS address ---------------------------------------------------------------


def test_obd_sid_on_uds_address_is_dropped_today(uds_physical):
    uds_physical.send(b"\x01\x2f")
    with pytest.raises(TimeoutError):
        uds_physical.recv()


@phase3
def test_obd_sid_on_uds_address_is_answered(uds_physical):
    uds_physical.send(b"\x01\x2f")
    assert uds_physical.recv() == FUEL_RESPONSE


# --- SIDs no protocol serves (DEV-06) --------------------------------------------------------


def test_unsupported_sid_on_uds_address_gets_no_response_today(uds_physical):
    uds_physical.send(b"\x22\xf1\x90")
    with pytest.raises(TimeoutError):
        uds_physical.recv()


@xfail_deviation("DEV-06", "unsupported SID should return NRC 0x11 serviceNotSupported")
def test_unsupported_sid_on_uds_address_gets_nrc_0x11(uds_physical):
    uds_physical.send(b"\x22\xf1\x90")
    assert uds_physical.recv() == b"\x7f\x22\x11"


def test_unsupported_sid_on_functional_address_gets_no_response(functional):
    # Unchanged by DEV-06: negative responses are not sent to functionally addressed requests.
    functional.send(b"\x22\xf1\x90")
    with pytest.raises(TimeoutError):
        functional.recv()
