"""The ELM327 response parser, against exchanges recorded in ELM327DSJ.

Sources, all from ELM327DSJ -- the revision recorded in modernization-plan.md section 7.1
and re-checked for decisions/0008:

* "Multiple PID Requests", page 45 -- the two worked CAN captures. Phase 5.1 already
  reproduces these byte for byte from the simulator's side; here they are parsed from the
  tester's side, which is the other half of the same evidence.
* "Error Messages and Alerts", pages 87-88 -- the error vocabulary.
* "Communicating with the ELM327", pages 8-9 -- CR termination with an optional linefeed,
  the '>' prompt, echo on by default, and the instruction to remove NUL bytes.

This module imports no pyserial and opens nothing, so it runs in CI where the [hardware]
extra is not installed.
"""

from __future__ import annotations

import pytest

from tests.hardware.elm327_parser import Elm327Error, clean, parse_response

# ELM327DSJ page 45, first worked capture: >01 04 05 0B 0C
DATASHEET_MULTILINE = "01 04 05 0B 0C\r00A\r0: 41 04 3F 05 44 0B\r1: 21 0C 17 B8 00 00 00\r\r>"
# ELM327DSJ page 45, second capture, same parameters requested in a different order
DATASHEET_MULTILINE_REORDERED = "01 0B 04 0C 05\r00A\r0: 41 0B 21 04 3F 0C\r1: 17 B8 05 44 00 00 00\r\r>"


def test_the_datasheet_multiline_capture_parses_to_its_ten_bytes():
    # The first line is the length, 00A = 10, so the trailing 00s on the last line are
    # ISO-TP padding beyond it and are not part of the response.
    assert parse_response(DATASHEET_MULTILINE, sent="01 04 05 0B 0C") == bytes.fromhex(
        "41043F05440B210C17B8"
    )


def test_the_datasheet_reordered_capture_parses_to_its_ten_bytes():
    assert parse_response(DATASHEET_MULTILINE_REORDERED, sent="01 0B 04 0C 05") == bytes.fromhex(
        "410B21043F0C17B80544"
    )


def test_a_single_line_response_parses():
    assert parse_response("0100\r41 00 1E 3F 80 13\r\r>", sent="0100") == bytes.fromhex("41001E3F8013")


def test_the_echoed_command_is_stripped():
    # Echo is on by default (E1), so the command comes back before the response.
    assert clean("0100\r41 00 1E\r\r>", sent="0100") == ["41 00 1E"]


def test_the_echo_is_stripped_however_it_was_spaced():
    # The device ignores spaces in input, so the echo may not match the string we sent.
    assert clean("01 00\r41 00 1E\r\r>", sent="0100") == ["41 00 1E"]


def test_nul_bytes_are_removed():
    # ELM327DSJ page 9: "if you are writing software for the ELM327, then ignore incoming
    # bytes that are of value 00 (ie. remove NULLs)". A stray NUL inside a hex string
    # would otherwise fail to parse.
    assert parse_response("0100\r\x0041 00 1E\r\r>", sent="0100") == bytes.fromhex("41001E")


def test_linefeeds_are_tolerated():
    # AT L1 adds a linefeed after every carriage return; AT L0 does not. Both must parse.
    assert parse_response("0100\r\n41 00 1E\r\n\r\n>", sent="0100") == bytes.fromhex("41001E")


def test_spaces_are_optional():
    # AT S0 turns off the printing of spaces.
    assert parse_response("0100\r41001E\r\r>", sent="0100") == bytes.fromhex("41001E")


@pytest.mark.parametrize(
    "message",
    ["NO DATA", "CAN ERROR", "BUS ERROR", "BUS BUSY", "DATA ERROR", "BUFFER FULL",
     "UNABLE TO CONNECT", "STOPPED", "LV RESET", "FB ERROR", "?"],
)
def test_every_datasheet_error_string_raises(message):
    with pytest.raises(Elm327Error) as caught:
        parse_response(f"3E80\r{message}\r\r>", sent="3E80")
    assert caught.value.message == message


def test_an_internal_error_code_raises():
    # ERRxx, page 88. ERR94 is the fatal CAN error and the one a bench is most likely to
    # meet, but the whole family is reported rather than swallowed.
    with pytest.raises(Elm327Error) as caught:
        parse_response("0100\rERR94\r\r>", sent="0100")
    assert caught.value.message == "ERR94"


@pytest.mark.parametrize("marker", ["<DATA ERROR", "<RX ERROR"])
def test_a_line_pointing_error_raises_without_its_marker(marker):
    # Page 87-88: these point at the line they refer to. The '<' is presentation, not part
    # of the condition's name.
    with pytest.raises(Elm327Error) as caught:
        parse_response(f"0100\r{marker}\r\r>", sent="0100")
    assert caught.value.message in ("DATA ERROR", "RX ERROR")


def test_no_data_is_reported_as_itself():
    # 0007 section 6.2 item 13: 3E 80 must produce the tester's own no-data condition, not
    # a response from the simulator. A caller has to tell that apart from a bus fault, so
    # the condition keeps its name rather than becoming a generic failure.
    with pytest.raises(Elm327Error) as caught:
        parse_response("3E80\rNO DATA\r\r>", sent="3E80")
    assert caught.value.message == "NO DATA"


def test_searching_is_not_an_error_and_is_dropped():
    # A protocol search prints SEARCHING... before the response arrives.
    assert parse_response("0100\rSEARCHING...\r41 00 1E\r\r>", sent="0100") == bytes.fromhex("41001E")


def test_a_multiline_length_shorter_than_the_data_truncates():
    # The length line governs; anything past it is ISO-TP padding.
    assert parse_response("0902\r003\r0: 49 02 01 FF FF FF\r\r>", sent="0902") == bytes.fromhex("490201")


def test_an_empty_response_raises_rather_than_returning_nothing():
    with pytest.raises(Elm327Error):
        parse_response("0100\r\r>", sent="0100")


def test_the_prompt_is_not_mistaken_for_data():
    # '>' is 0x3E, which is also a valid hex digit pair's worth of nothing. It must never
    # reach bytes.fromhex.
    assert parse_response("0100\r41 00 1E\r>", sent="0100") == bytes.fromhex("41001E")
