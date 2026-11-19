import pytest

pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer

from ecu_simulator.gui.server import application


@pytest.mark.asyncio
async def test_control_api_restricts_browser_actions_and_requires_run_id(tmp_path):
    async with TestClient(TestServer(application(tmp_path))) as client:
        origin = str(client.make_url("/")).rstrip("/")
        assert (await client.get("/run")).status == 200
        assert (await client.get("/run", headers={"Host": "foreign.test"})).status == 421
        assert (await client.post("/run", json={"text": "bad"}, headers={"Origin": "http://foreign.test"})).status == 403
        for path, status in [("/run/send", 400), ("/run/stop", 400), ("/run/old/stop", 409), ("/run", 400)]:
            response = await client.post(path, json={"text": "["}, headers={"Origin": origin})
            assert response.status == status
        assert "ice_default" in (await (await client.get("/profiles")).json())["shipped"]
