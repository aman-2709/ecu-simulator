"""Run the physical-interface review branch on an isolated virtual SocketCAN bus."""

import asyncio
import socket
import struct
from pathlib import Path

import pytest

pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")
from aiohttp.test_utils import TestClient, TestServer

from ecu_simulator.cli import default_profile_path
from ecu_simulator.gui import dbc_workflow
from ecu_simulator.gui.server import application


@pytest.mark.asyncio
async def test_profile_review_observes_conflict_and_binds_start_to_review(vcan, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(dbc_workflow, "virtual_can", lambda _: False)
    dbc = Path(__file__).parents[1] / "fixtures/dbc/ford_fusion_2018_pt.dbc"
    async with TestClient(TestServer(application())) as client:
        origin = str(client.make_url("/")).rstrip("/")

        async def post(url, body):
            return await client.post(url, json=body, headers={"Origin": origin})

        uploaded = await client.post("/dbc/import", params={"filename": dbc.name},
                                     data=dbc.read_bytes(), headers={"Origin": origin})
        assert uploaded.status == 200
        catalog = await uploaded.json()
        entry = {"name": "EPAS_INFO", "period_ms": 100, "values": {"SteMdule_U_Meas": 12}}
        configuration = {"filename": dbc.name, "sha256": catalog["sha256"], "messages": [entry]}
        text = dbc_workflow.changed_draft(default_profile_path().read_text().replace("vcan0", vcan), configuration)
        assert (await post("/run", {"text": text})).status == 400
        with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as node:
            node.bind((vcan,))
            async def transmit():
                while True:
                    node.send(struct.pack("=IB3x8s", 0x82, 8, bytes(8)))
                    await asyncio.sleep(.05)
            task = asyncio.create_task(transmit())
            try:
                response = await post("/dbc/review", {"text": text})
                assert response.status == 200
                review = await response.json()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert review["required"] and review["interface"] == vcan
        assert review["conflicts"] == [f"0x082 already observed on {vcan}"]
        reviewed = review["messages"][0]
        assert reviewed["raw"] == "80 00 14 00 78 00 00 00"
        assert reviewed["values"]["SteMdule_U_Meas"] == 12
        assert "SteMdule_I_Est" in reviewed["values"]
        start = await post("/run", {"text": text, "review": review["token"]})
        assert start.status == 202
        assert (await post("/run", {"text": text, "review": review["token"]})).status == 400
