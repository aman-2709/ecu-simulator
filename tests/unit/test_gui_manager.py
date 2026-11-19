import asyncio
import sys

import pytest

from ecu_simulator.cli import default_profile_path
from ecu_simulator.gui.manager import Manager, RunConflict


async def until(manager, state):
    async with asyncio.timeout(3):
        while manager.status()["state"] != state:
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_ready_exit_and_retry():
    script = "import time; print('{\u0022ready\u0022:true}', flush=True); time.sleep(.15)"
    manager = Manager(child_command=(sys.executable, "-c", script))
    text = default_profile_path().read_text()
    first = await manager.start(text)
    assert first["state"] == "Starting"
    with pytest.raises(RunConflict):
        await manager.start(text)
    await until(manager, "Running")
    await until(manager, "Failed")
    assert "exited" in manager.status()["error"]
    second = await manager.start(text)
    assert second["id"] != first["id"]
    with pytest.raises(RunConflict):
        await manager.stop(first["id"])
    await manager.stop(second["id"])
    assert manager.status()["state"] == "Stopped"


@pytest.mark.asyncio
async def test_startup_times_out_and_reaps_uncooperative_child():
    script = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(20)"
    manager = Manager(child_command=(sys.executable, "-c", script), startup_timeout=.2, stop_timeout=.1)
    await manager.start(default_profile_path().read_text())
    await until(manager, "Failed")
    assert "timed out" in manager.status()["error"]


@pytest.mark.asyncio
async def test_failed_bind_names_interface_then_allows_retry():
    manager = Manager()
    text = default_profile_path().read_text().replace("vcan0", "missing_can")
    first = await manager.start(text)
    await until(manager, "Failed")
    assert "missing_can" in manager.status()["error"]
    second = await manager.start(text)
    assert second["id"] != first["id"]
    await manager.stop(second["id"])


@pytest.mark.asyncio
async def test_one_exchange_and_stale_actions_leave_current_child_alone(monkeypatch):
    started = asyncio.Event()
    calls = []
    async def exchange(interface, endpoint, payload):
        calls.append(payload)
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("ecu_simulator.gui.manager.exchange", exchange)
    manager = Manager(child_command=(sys.executable, "-c", "import time; print('{\u0022ready\u0022:true}',flush=True); time.sleep(30)"))
    text = default_profile_path().read_text()
    first = await manager.start(text)
    await manager.stop(first["id"])
    current = await manager.start(text)
    await until(manager, "Running")
    send = asyncio.create_task(manager.send(current["id"], "engine.uds_physical", "3E 80"))
    await started.wait()
    with pytest.raises(RunConflict, match="progress"):
        await manager.send(current["id"], "engine.uds_physical", "19 02 FF")
    with pytest.raises(RunConflict, match="stale"):
        await manager.send(first["id"], "engine.uds_physical", "19 02 FF")
    with pytest.raises(RunConflict, match="stale"):
        await manager.stop(first["id"])
    assert not send.done() and calls == [bytes.fromhex("3E 80")]
    await manager.stop(current["id"])
    with pytest.raises(RunConflict, match="cancelled"):
        await send
