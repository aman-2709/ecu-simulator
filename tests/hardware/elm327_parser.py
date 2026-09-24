"""Parse what an ELM327 prints, per ELM327DSJ.

Pure and I/O-free on purpose. It is imported by unit tests that run in CI, where the
optional ``[hardware]`` extra is not installed, so it must not import pyserial. That split
is what confines the part of the harness which cannot be tested without a dongle to the
serial I/O alone.

The shapes it handles, all from ELM327DSJ:

* a single line of hex bytes, with or without spaces (``AT S0`` / ``AT S1``);
* a multi-line response, where the first line is the ISO-TP length in hex and the following
  lines are numbered ``0:``, ``1:`` -- page 45: "the first line tells us that it is 00A
  (decimal 10) bytes long, so we only pay attention to the first ten bytes of the following
  lines (and ignore the final three 00's on the last line)";
* the error vocabulary on pages 87-88.

Three device behaviours it absorbs, each of which would otherwise corrupt a byte
comparison: the command is echoed back because ``E1`` is the default; a linefeed may or may
not follow each carriage return depending on ``AT L1`` / ``AT L0``; and NUL bytes may appear
anywhere -- page 9 tells software authors to remove them.
"""

from __future__ import annotations

import re

PROMPT = ">"

#: Conditions the device reports instead of a response (ELM327DSJ pages 87-88).
ERROR_MESSAGES: tuple[str, ...] = (
    "NO DATA",
    "CAN ERROR",
    "BUS ERROR",
    "BUS BUSY",
    "DATA ERROR",
    "RX ERROR",
    "BUFFER FULL",
    "UNABLE TO CONNECT",
    "STOPPED",
    "LV RESET",
    "FB ERROR",
    "ACT ALERT",
    "!ACT ALERT",
    "?",
)

_ERR_CODE = re.compile(r"^ERR[0-9A-F]{2}$", re.IGNORECASE)
_LINE_INDEX = re.compile(r"^[0-9A-F]:\s*", re.IGNORECASE)
_HEX_LENGTH = re.compile(r"^[0-9A-F]{3}$", re.IGNORECASE)


class Elm327Error(Exception):
    """The device reported a condition instead of a response.

    ``message`` keeps the device's own wording, because a caller needs to tell ``NO DATA``
    -- which is the expected answer to a suppressed request -- apart from ``CAN ERROR``,
    which means the bench is broken.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def clean(raw: str, sent: str) -> list[str]:
    """Strip NULs, the echoed command, search notices and the prompt; return what is left."""
    text = raw.replace("\x00", "")
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    normalised_sent = sent.replace(" ", "").upper()

    lines: list[str] = []
    for chunk in text.split("\r"):
        line = chunk.strip()
        if line.endswith(PROMPT):
            line = line[: -len(PROMPT)].strip()
        if not line:
            continue
        if not lines and line.replace(" ", "").upper() == normalised_sent:
            continue  # the echo, which only ever precedes the response
        if line.upper().startswith("SEARCHING"):
            continue
        lines.append(line)
    return lines


def parse_response(raw: str, sent: str) -> bytes:
    """The response payload the device reported.

    Raises :class:`Elm327Error` for any reported condition, so a caller can assert on a
    named condition rather than inferring one from an empty result.
    """
    lines = clean(raw, sent)
    if not lines:
        raise Elm327Error("no response")

    for line in lines:
        upper = line.upper().lstrip("<").strip()
        if upper in ERROR_MESSAGES or _ERR_CODE.match(upper):
            raise Elm327Error(upper)

    if len(lines) > 1 and _HEX_LENGTH.match(lines[0]):
        declared = int(lines[0], 16)
        body = "".join(_LINE_INDEX.sub("", line).replace(" ", "") for line in lines[1:])
        return bytes.fromhex(body)[:declared]

    return bytes.fromhex("".join(line.replace(" ", "") for line in lines))
