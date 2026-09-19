"""DEV-03 guard: the ECU-name bytes must not move while the OBD layer is restructured.

Service 09 PID 0A is deferred (docs/decisions/0003-phase-5-obd-evidence.md): the exact
byte layout is not independently corroborated, so its current bytes stay frozen, wrong or
not. This file exists so that a Mode 09 refactor cannot change them by accident. It pins
the complete response, not a prefix.

Do not "fix" these bytes. They change only when DEV-03 is unblocked by the J1979 or
J1979-DA text, or by two or more independent captures of a complete PID 0A payload.
"""

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.protocols.obd import ObdProtocol

# 49 0A, then 20 bytes: seven leading NULs and then the 13-character shipped ECU name.
# No count byte. Captured from commit ce46b87 and unchanged since.
FROZEN_PID0A_RESPONSE = bytes.fromhex("490a") + bytes(7) + b"ECU_SIMULATOR"


def ecu_name_response():
    config = app.RuntimeConfig.build(load_profile(default_profile_path()))
    ecu = config.profile.ecus["engine"]
    protocol = ObdProtocol(app.build_vehicle(config), ecu_name=ecu.name, dtcs=[d.code for d in ecu.dtcs])
    return protocol.handle(ServiceRequest(b"\x09\x0a"))


def test_mode09_pid0a_response_is_byte_for_byte_unchanged():
    assert ecu_name_response() == FROZEN_PID0A_RESPONSE


def test_mode09_pid0a_response_length_is_unchanged():
    assert len(FROZEN_PID0A_RESPONSE) == 22
    assert len(ecu_name_response()) == 22


def test_mode09_pid0a_has_no_item_count_byte():
    # The third byte is the first NUL of the padded name, not a count of data items.
    response = ecu_name_response()
    assert response[:2] == b"\x49\x0a"
    assert response[2] == 0x00
    assert response[2:9] == bytes(7)


def test_mode09_pid0a_name_is_left_padded_not_right_padded():
    # The one available capture suggests real ECUs do not left-pad. That is precisely
    # what is unresolved; until it is, this asserts what this project actually emits.
    response = ecu_name_response()
    assert response.endswith(b"ECU_SIMULATOR")
    assert not response[2:].startswith(b"ECU_SIMULATOR")
