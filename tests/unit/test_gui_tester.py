import asyncio
import socket

import pytest

from ecu_simulator.gui.tester import SUPPRESSED, decode, transact


def test_decoder_distinguishes_complete_and_partial_records():
    reply = decode(bytes.fromhex("59 02 8C 00 01 01 08 94 77 01 04"))
    assert reply["raw"] == "59 02 8C 00 01 01 08 94 77 01 04"
    assert reply["records"] == ["P0001 · Confirmed · failure type 01 · status 08", "B1477 · Pending · failure type 01 · status 04"]
    assert decode(bytes.fromhex("59 02 8C"))["records"] == []
    assert "Incomplete" in decode(bytes.fromhex("59 02 8C 00"))["message"]
    assert decode(b"\x62")["raw"] == "62"


class TestSocket:
    __test__ = False

    def __init__(self, sock):
        self.sock = sock

    def fileno(self):
        return self.sock.fileno()

    def send(self, data):
        return bool(self.sock.send(data))

    def recv(self):
        try:
            return self.sock.recv(128)
        except BlockingIOError:
            return None


@pytest.mark.asyncio
@pytest.mark.parametrize("hex_input,negative,expected", [
    ("3E 80", False, SUPPRESSED), ("3E 80", True, "Negative response · NRC 22"),
    ("19 02 FF", False, "Response timed out"), ("22 F1 90", False, "Response timed out")])
async def test_wait_until_reply_or_deadline(hex_input, negative, expected):
    a, b = socket.socketpair(type=socket.SOCK_DGRAM)
    with a, b:
        a.setblocking(False)
        task = asyncio.create_task(transact(TestSocket(a), bytes.fromhex(hex_input), .12))
        await asyncio.sleep(.04)
        assert not task.done()
        assert b.recv(128) == bytes.fromhex(hex_input)
        if negative:
            b.send(bytes.fromhex("7F 3E 22"))
        result = await task
        assert result["message"] == expected
        assert result["raw"] == ("7F 3E 22" if negative else "")
