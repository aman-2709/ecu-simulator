"""The thin handlers must reproduce the removed listeners' request handling byte for byte."""

import pytest

from ecu_simulator.obd import handler as obd_handler
from ecu_simulator.obd import responses
from ecu_simulator.uds import handler as uds_handler


@pytest.fixture
def reset_speed(monkeypatch):
    monkeypatch.setattr(responses, "vehicle_speed", 0)


@pytest.mark.parametrize(
    "payload, expected",
    [
        (None, (None, None)),
        (b"", (None, None)),
        (b"\x01", (None, 0x01)),
        (b"\x01\x0d", (0x0D, 0x01)),
        (b"\x01\x0d\x0c", (0x0D, 0x01)),
    ],
)
def test_obd_sid_pid_parsing_matches_legacy_listener(payload, expected):
    assert obd_handler.get_sid_and_pid(payload) == expected


def test_obd_handler_answers_speed_and_vin(reset_speed):
    assert obd_handler.handle(b"\x01\x0d") == b"\x41\x0d\x00"
    assert obd_handler.handle(b"\x09\x02") == b"\x49\x02\x00TESTVIN0123456789"


def test_obd_handler_multi_pid_request_answers_only_the_first_pid(reset_speed):
    # DEV-18 preserved: ISO 15765-4 allows several PIDs per request; only request[1] is used.
    assert obd_handler.handle(b"\x01\x0d\x2f\x51") == b"\x41\x0d\x00"


@pytest.mark.parametrize("payload", [b"", b"\x01\x0c", b"\x04", b"\x0b\x00"])
def test_obd_handler_sends_nothing_for_empty_unsupported_or_invalid(payload):
    assert obd_handler.handle(payload) is None


def test_obd_handler_returns_immutable_bytes():
    response = obd_handler.handle(b"\x01\x51")
    assert type(response) is bytes and response == b"\x41\x51\x01"


def test_uds_handler_answers_session_control_and_negative_responses():
    assert uds_handler.handle(b"\x10\x01") == b"\x50\x01\x00\x1e\x0b\xb8"
    assert uds_handler.handle(b"\x19\x02\xff") == b"\x7f\x19\x13"


@pytest.mark.parametrize("payload", [b"", b"\x22\xf1\x90", b"\x3e\x00"])
def test_uds_handler_sends_nothing_for_empty_or_unsupported(payload):
    assert uds_handler.handle(payload) is None
