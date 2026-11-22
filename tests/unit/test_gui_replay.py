import struct

import pytest

from ecu_simulator.gui import replay


@pytest.mark.parametrize("lines", [
    b"", b"(1.0) can0 800#00", b"(1.0) can0 20000000#00",
    b"(1.0) can0 123##011", b"(1.0) can0 123 [4] 01",
    b"(1.0) can0 123#F", b"(1.0) can0 123#\n(0.9) can0 123#",
    b"(1.0) can0 123#\n(2.0) can1 123#",
])
def test_reject_invalid_recordings(lines):
    with pytest.raises(ValueError):
        replay.parse_recording(lines)


def test_remote_request_keeps_extended_flag_and_requested_length():
    rec = replay.parse_recording(b"(1.000000000) can0 000000AB#R7")
    assert rec.frames == [(0, struct.pack("=IB3x8s", 0xC00000AB, 7, b""))]


def test_replay_requires_virtual_bus(monkeypatch):
    monkeypatch.setattr(replay, "virtual_can", lambda interface: False)
    with pytest.raises(ValueError, match="virtual"):
        replay.Replay().start(replay.parse_recording(b"(0) can1 123#"), "log", False)
