"""Stateful service exchanges through the ECU process and the kernel ISO-TP sockets."""

import pytest

from tests.integration.conftest import open_tester_socket


@pytest.fixture
def link(vcan, simulator):
    sock = open_tester_socket(vcan, rx_id=0x7E9, tx_id=0x7E1)
    try:
        yield sock
    finally:
        sock.close()


def exchange(link, request, expected):
    link.send(bytes.fromhex(request))
    assert link.recv() == bytes.fromhex(expected)


def test_transfer_round_trip_and_retry(link):
    exchange(link, "3D 22 10 00 00 04 11 22 33 44", "7D 22 10 00 00 04")
    exchange(link, "34 00 22 10 00 00 04", "74 10 42")
    exchange(link, "36 01 AA BB", "76 01")
    exchange(link, "36 01 AA BB", "76 01")
    exchange(link, "36 03 CC DD", "7F 36 73")
    exchange(link, "37", "7F 37 24")
    exchange(link, "23 22 10 00 00 04", "63 11 22 33 44")
    exchange(link, "36 02 CC DD", "76 02")
    exchange(link, "37 FF FF FF FF", "7F 37 72")
    exchange(link, "37", "77")
    exchange(link, "23 22 10 00 00 04", "63 AA BB CC DD")
    exchange(link, "35 00 22 10 00 00 04", "75 10 42")
    exchange(link, "36 01", "76 01 AA BB CC DD")
    exchange(link, "36 01", "76 01 AA BB CC DD")
    exchange(link, "37", "77")
    exchange(link, "23 22 FF FF 00 04", "7F 23 31")


def test_periodic_can_reports_and_communication_control(link):
    exchange(link, "28 01 01", "68 01")
    exchange(link, "2A 03 02", "6A")
    with pytest.raises(TimeoutError):
        link.recv()
    exchange(link, "28 00 01", "68 00")
    report = link.recv()
    assert report[:2] == b"\x6a\x02" and len(report) == 3
    exchange(link, "2A 04", "6A")
    with pytest.raises(TimeoutError):
        link.recv()


def test_routine_timing_and_dtc_controls(link):
    exchange(link, "31 03 01 00", "7F 31 24")
    exchange(link, "31 01 01 00", "71 01 01 00")
    exchange(link, "31 03 01 00", "71 03 01 00 01")
    exchange(link, "31 02 01 00", "71 02 01 00")
    exchange(link, "31 03 01 00", "71 03 01 00 00")
    exchange(link, "83 04 00 32 01 F4", "C3 04")
    exchange(link, "83 03", "C3 03 00 32 01 F4")
    exchange(link, "83 02", "C3 02")
    exchange(link, "83 03", "C3 03 00 1E 0B B8")
    exchange(link, "85 02", "C5 02")
    exchange(link, "85 01", "C5 01")
