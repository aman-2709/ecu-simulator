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
