"""Drive an ELM327-compatible adapter over a serial port.

Deliberately thin. This is the only component of the hardware harness whose correctness a
real dongle could still disprove, so it does as little as possible and delegates every
decision about the bytes to :mod:`tests.hardware.elm327_parser`.

Read-until-prompt rather than read-line, for two reasons from ELM327DSJ page 8: software
"should always wait for either the prompt character ('>' or hex 3E) ... before beginning to
send the next command", and a multi-line response contains several carriage returns, so a
line-based read cannot know when a response has ended.

Satisfies :class:`tests.hardware.tester.DiagnosticTester`, so the same acceptance cases run
against this and against the fake dongle on vcan.
"""

from __future__ import annotations

import serial

from tests.hardware.elm327_parser import PROMPT, Elm327Error, clean, parse_response

#: ELM327DSJ page 8: 38400 baud unless PP 0C was changed, or 9600 if pin 6 was low at
#: power up. Both are real, so the value is configurable and recorded in the bench report.
DEFAULT_BAUD = 38400


class Elm327:
    """A physical adapter on a serial device -- USB, or a Bluetooth rfcomm binding."""

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, timeout: float = 5.0) -> None:
        # 8N1, per ELM327DSJ page 8: "8 data bits, no parity bits, and 1 stop bit".
        self.port = serial.Serial(
            port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
        )

    def command(self, text: str) -> str:
        """Send one command and return everything up to and including the prompt."""
        self.port.reset_input_buffer()
        self.port.write(text.encode("ascii") + b"\r")
        self.port.flush()
        raw = self.port.read_until(PROMPT.encode("ascii")).decode("ascii", errors="replace")
        if PROMPT not in raw:
            # Leave nothing behind: a partially-read answer would surface as the next
            # command's response and be far harder to diagnose than this timeout.
            self.port.reset_input_buffer()
            raise TimeoutError(
                f"no prompt within {self.port.timeout}s after {text!r}; got {raw!r}. "
                "A silent adapter is also what a bitrate mismatch, an unpowered dongle or "
                "the wrong serial port look like -- check those before the simulator."
            )
        return raw

    def at(self, text: str) -> str:
        """An AT command's answer, as one line of text."""
        lines = clean(self.command(text), sent=text)
        if not lines:
            raise Elm327Error("no response")
        return lines[0]

    def ask(self, text: str) -> bytes:
        """An OBD or UDS request's response payload."""
        return parse_response(self.command(text), sent=text)

    def close(self) -> None:
        """Release the port. Safe to call more than once."""
        if self.port.is_open:
            self.port.close()
