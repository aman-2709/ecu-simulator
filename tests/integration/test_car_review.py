"""Run the whole-car review and start on an isolated virtual SocketCAN bus, with the physical-interface rules applied."""

import asyncio
import socket
import struct

import pytest
from aiohttp.test_utils import TestClient, TestServer

from ecu_simulator.cli import default_profile_path
from ecu_simulator.gui import dbc_workflow
from ecu_simulator.gui.server import application


@pytest.mark.asyncio
async def test_car_review_observes_conflict_binds_start_to_review_and_status_stays_small(vcan, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(dbc_workflow, "virtual_can", lambda _: False)
    text = default_profile_path().with_name("car_toyota_demo.yaml").read_text().replace("vcan0", vcan)
    async with TestClient(TestServer(application())) as client:
        origin = str(client.make_url("/")).rstrip("/")

        async def post(url, body):
            response = await client.post(url, json=body, headers={"Origin": origin})
            return response.status, await response.json()

        status, body = await post("/run", {"text": text})
        assert status == 400 and "review" in body["error"]
        with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as node:
            node.bind((vcan,))

            async def transmit():
                while True:
                    node.send(struct.pack("=IB3x8s", 0xAA, 8, bytes(8)))
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(transmit())
            try:
                status, review = await post("/car/review", {"text": text})
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert status == 200 and review["required"] and review["interface"] == vcan
        assert review["conflicts"] == [f"0x0AA already observed on {vcan}"]
        wheels = next(m for m in review["messages"] if m["name"] == "WHEEL_SPEEDS")
        assert wheels["id"] == 0xAA and len(wheels["raw"].split()) == 8
        assert "not a recording" in review["note"]

        status, started = await post("/run", {"text": text, "review": review["token"]})
        assert status == 202
        assert (await post("/run", {"text": text, "review": review["token"]}))[0] == 400
        try:
            async with asyncio.timeout(15):
                while (state := await (await client.get("/run")).json())["state"] != "Running":
                    assert state["state"] != "Failed", state["error"]
                    await asyncio.sleep(0.1)
            assert state["car"]["platform"] == "TOYOTA_COROLLA" and "messages" not in state["car"]
            counts = state["car"]["counts"]
            whole = await (await client.get("/car/run")).json()
            assert sum(counts.values()) == len(whole["messages"]) > 20
            assert {m["name"] for m in whole["messages"]} >= {"WHEEL_SPEEDS", "GAS_PEDAL"}
            assert len(await (await client.get("/run")).text()) < 2000  # polled every 350 ms: no message list in it
        finally:
            await post(f"/run/{started['id']}/stop", {})
