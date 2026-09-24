"""The ELM327 serial driver, against a scripted fake over a pseudo-terminal.

A pty is a real tty, so pyserial opens and configures it exactly as it would a USB device
or an rfcomm binding. That makes every part of the driver except the physical link testable
with no hardware at all.

The whole module skips when the optional ``[hardware]`` extra is absent, which is what CI
records. It never touches a CAN interface.
"""

from __future__ import annotations

import pytest

serial = pytest.importorskip(
    "serial", reason="needs the optional [hardware] extra: pip install -e '.[dev,hardware]'"
)

from tests.hardware.elm327_parser import Elm327Error  # noqa: E402
from tests.hardware.elm327_serial import DEFAULT_BAUD, Elm327  # noqa: E402
from tests.hardware.fake_elm327 import FakeElm327  # noqa: E402
from tests.hardware.tester import DiagnosticTester  # noqa: E402

RESPONSES = {
    "ATZ": "\r\rELM327 v1.4b\r\r>",
    "ATI": "ELM327 v1.4b\r\r>",
    "AT@1": "OBDII to RS232 Interpreter\r\r>",
    "ATE0": "OK\r\r>",
    "ATSP6": "OK\r\r>",
    "ATDPN": "6\r\r>",
    "ATRV": "12.5V\r\r>",
    "0100": "41 00 1E 3F 80 13\r\r>",
    "010C": "41 0C 0C 80\r\r>",
    "3E80": "NO DATA\r\r>",
    "0104050B0C": "00A\r0: 41 04 3F 05 44 0B\r1: 21 0C 17 B8 00 00 00\r\r>",
}


@pytest.fixture
def fake():
    device = FakeElm327(RESPONSES)
    device.start()
    try:
        yield device
    finally:
        device.stop()


@pytest.fixture
def elm(fake):
    device = Elm327(fake.port, baud=DEFAULT_BAUD, timeout=2.0)
    try:
        yield device
    finally:
        device.close()


# --- the seam ---------------------------------------------------------------------------


def test_the_driver_satisfies_the_diagnostic_tester_protocol(elm):
    # Task 6's seam: the physical backend must be interchangeable with the simulated one.
    assert isinstance(elm, DiagnosticTester)


# --- prompt detection -------------------------------------------------------------------


def test_identity_is_read_back(elm):
    assert elm.at("AT I") == "ELM327 v1.4b"


def test_device_description_is_read_back(elm):
    assert elm.at("AT @1") == "OBDII to RS232 Interpreter"


def test_protocol_number_is_read_back(elm):
    assert elm.at("AT DPN") == "6"


def test_supply_voltage_is_read_back(elm):
    assert elm.at("AT RV") == "12.5V"


def test_a_response_is_read_up_to_the_prompt_and_no_further(elm, fake):
    # read_until must stop at '>', not consume whatever the next command would produce.
    elm.ask("01 00")
    assert elm.ask("01 0C") == bytes.fromhex("410C0C80")


# --- echo and NUL handling --------------------------------------------------------------


def test_the_echoed_command_does_not_reach_the_payload(elm):
    # E1 is the device default, so every answer arrives behind its own command.
    assert elm.ask("01 00") == bytes.fromhex("41001E3F8013")


def test_an_injected_nul_does_not_corrupt_the_payload(elm, fake):
    # ELM327DSJ page 9 warns a NUL may appear anywhere in the stream.
    fake.inject_nul = True
    assert elm.ask("01 0C") == bytes.fromhex("410C0C80")


def test_a_multiline_response_returns_parsed_bytes(elm):
    assert elm.ask("01 04 05 0B 0C") == bytes.fromhex("41043F05440B210C17B8")


# --- reported conditions ----------------------------------------------------------------


def test_no_data_raises_rather_than_returning_empty(elm):
    with pytest.raises(Elm327Error) as caught:
        elm.ask("3E 80")
    assert caught.value.message == "NO DATA"


def test_an_unknown_command_raises_the_question_mark(elm):
    with pytest.raises(Elm327Error) as caught:
        elm.ask("ZZ")
    assert caught.value.message == "?"


# --- timeouts ---------------------------------------------------------------------------


def test_a_device_that_never_prompts_times_out_rather_than_hanging(fake):
    fake.responses["0100"] = "41 00 1E"  # no prompt: the response never terminates
    device = Elm327(fake.port, baud=DEFAULT_BAUD, timeout=0.3)
    try:
        with pytest.raises(TimeoutError):
            device.ask("01 00")
    finally:
        device.close()


def test_the_timeout_message_names_what_else_looks_like_this(fake):
    # On bench day a silent device is also what a bitrate mismatch, an unpowered dongle
    # and the wrong serial port look like. The message has to say so.
    fake.responses["0100"] = "41 00 1E"
    device = Elm327(fake.port, baud=DEFAULT_BAUD, timeout=0.3)
    try:
        with pytest.raises(TimeoutError, match="bitrate|unpowered|serial port"):
            device.ask("01 00")
    finally:
        device.close()


def test_a_timeout_does_not_wedge_the_driver(fake):
    # The next command must work: a timed-out read cannot leave stale bytes behind.
    fake.responses["0100"] = "41 00 1E"
    device = Elm327(fake.port, baud=DEFAULT_BAUD, timeout=0.3)
    try:
        with pytest.raises(TimeoutError):
            device.ask("01 00")
        assert device.ask("01 0C") == bytes.fromhex("410C0C80")
    finally:
        device.close()


# --- framing and shutdown ---------------------------------------------------------------


def test_the_command_is_terminated_with_a_carriage_return(elm, fake):
    # ELM327DSJ page 8: every message must end with CR before it is acted upon.
    elm.at("AT I")
    assert fake.received[-1].endswith("\r"), fake.received


def test_a_bare_carriage_return_is_never_sent(elm, fake):
    # Page 9: a lone CR repeats the previous command. An automated driver that emitted one
    # would silently re-run the last request.
    elm.at("AT I")
    elm.ask("01 0C")
    assert "\r" not in [line.strip("\r") + "\r" for line in fake.received if line == "\r"]
    assert all(line.strip() for line in fake.received), fake.received


def test_the_port_is_opened_8n1(elm):
    assert (elm.port.bytesize, elm.port.parity, elm.port.stopbits) == (8, "N", 1)


def test_the_baud_rate_is_configurable(fake):
    device = Elm327(fake.port, baud=9600, timeout=1.0)
    try:
        assert device.port.baudrate == 9600
    finally:
        device.close()


def test_the_default_baud_is_the_datasheet_default():
    # Page 8: 38400 unless PP 0C was changed, or 9600 if pin 6 was low at power up.
    assert DEFAULT_BAUD == 38400


def test_close_releases_the_port(fake):
    device = Elm327(fake.port, baud=DEFAULT_BAUD, timeout=1.0)
    assert device.port.is_open
    device.close()
    assert not device.port.is_open


def test_close_is_idempotent(fake):
    # A bench run aborts in the middle often enough that a second close must not raise.
    device = Elm327(fake.port, baud=DEFAULT_BAUD, timeout=1.0)
    device.close()
    device.close()
    assert not device.port.is_open
