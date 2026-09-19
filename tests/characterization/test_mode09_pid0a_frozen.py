"""DEV-03 guard: the ECU-name bytes must not move while the OBD layer is restructured.

Service 09 PID 0A is deferred (docs/decisions/0003-phase-5-obd-evidence.md): the exact
byte layout is not independently corroborated, so its current bytes stay frozen, wrong or
not. This file exists so that a Mode 09 refactor cannot change them by accident. It pins
the complete response, not a prefix.

Do not "fix" these bytes. They change only when DEV-03 is unblocked by the J1979 or
J1979-DA text, or by two or more independent captures of a complete PID 0A payload.
"""

from ecu_simulator.obd import handler

# 49 0A, then 20 bytes: seven leading NULs and then the 13-character shipped ECU name.
# No count byte. Captured from commit ce46b87 and unchanged since.
FROZEN_PID0A_RESPONSE = bytes.fromhex("490a") + bytes(7) + b"ECU_SIMULATOR"


def test_mode09_pid0a_response_is_byte_for_byte_unchanged():
    assert handler.handle(b"\x09\x0a") == FROZEN_PID0A_RESPONSE


def test_mode09_pid0a_response_length_is_unchanged():
    assert len(FROZEN_PID0A_RESPONSE) == 22
    assert len(handler.handle(b"\x09\x0a")) == 22


def test_mode09_pid0a_has_no_item_count_byte():
    # The third byte is the first NUL of the padded name, not a count of data items.
    response = handler.handle(b"\x09\x0a")
    assert response[:2] == b"\x49\x0a"
    assert response[2] == 0x00
    assert response[2:9] == bytes(7)


def test_mode09_pid0a_name_is_left_padded_not_right_padded():
    # The one available capture suggests real ECUs do not left-pad. That is precisely
    # what is unresolved; until it is, this asserts what this project actually emits.
    response = handler.handle(b"\x09\x0a")
    assert response.endswith(b"ECU_SIMULATOR")
    assert not response[2:].startswith(b"ECU_SIMULATOR")
