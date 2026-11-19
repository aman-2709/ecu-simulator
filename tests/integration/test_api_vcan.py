"""decisions/0010 §9.3: a real kernel ISO-TP request produces the matching WebSocket event.

The vcan fixture runs before aiohttp is looked for, so on a hosted runner this skips with
the CAN_ISOTP reason, which is the one that matters (0009)."""

import asyncio
import json
import socket

import pytest

from tests.integration.conftest import FunctionalTester, Simulator


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_an_isotp_request_appears_on_the_websocket(vcan, tmp_path):
    aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")
    port = free_port()
    sim = Simulator(vcan, str(tmp_path), extra_args=["--api", f"127.0.0.1:{port}"])
    tester = None
    try:
        sim.wait_ready()
        tester = FunctionalTester(vcan, 0x7DF, 0x7E8, 0x7E0)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"http://127.0.0.1:{port}/api/v1/events",
                                          origin=f"http://127.0.0.1:{port}") as ws:
                kinds = [json.loads((await asyncio.wait_for(ws.receive(), 3)).data)["type"] for _ in range(2)]
                assert kinds == ["hello", "state"]
                await asyncio.to_thread(tester.send, b"\x01\x0c")
                reply = await asyncio.to_thread(tester.recv)
                while (event := json.loads((await asyncio.wait_for(ws.receive(), 3)).data))["type"] == "dropped":
                    pass                                        # the 4 Hz one-slot notice may come first
        assert reply[:2] == b"\x41\x0c"
        assert (event["type"], event["request"], event["response"]) == ("exchange", "010c", reply.hex())
        assert (event["rx_id"], event["tx_id"], event["functional"], event["outcome"]) == ("0x7df", "0x7e8", True, "responded")
    finally:
        if tester is not None:
            tester.close()
        sim.terminate()
