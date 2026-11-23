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
    async def exchange(interface, endpoint, payload, reply):
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


@pytest.mark.asyncio
async def test_run_scoped_broadcast_control_is_acknowledged_by_the_child():
    script = """
import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"ready": True, "restbus": {"period_ms":20}}), flush=True)
for line in sys.stdin:
    print(line.strip(), flush=True)
"""
    manager = Manager(child_command=(sys.executable, "-c", script))
    run = await manager.start(default_profile_path().read_text())
    try:
        await until(manager, "Running")
        with pytest.raises(RunConflict, match="stale"):
            await manager.broadcast("previous", True)
        assert (await manager.broadcast(run["id"], True))["restbus"]["paused"] is True
        assert (await manager.broadcast(run["id"], False))["restbus"]["paused"] is False
    finally:
        await manager.close()
    assert manager.status()["restbus"] is None


@pytest.mark.asyncio
async def test_cancelled_pause_ack_cannot_complete_next_resume():
    script = """
import sys
sys.stdin.readline()
print('{"ready":true,"restbus":{"period_ms":20}}', flush=True)
first = sys.stdin.readline()
second = sys.stdin.readline()
print(first, end='', flush=True)
print(second, end='', flush=True)
sys.stdin.read()
"""
    manager = Manager(child_command=(sys.executable, "-c", script))
    run = await manager.start(default_profile_path().read_text())
    try:
        await until(manager, "Running")
        pause = asyncio.create_task(manager.broadcast(run["id"], True))
        async with asyncio.timeout(2):
            while manager.broadcast_ack is None:
                await asyncio.sleep(0)
        pause.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pause
        assert (await manager.broadcast(run["id"], False))["restbus"]["paused"] is False
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_status_includes_car_metadata_while_running():
    car = {"platform": "TOYOTA_COROLLA", "dbc": {"filename": "c.dbc", "sha256": "ab" * 32, "name": "c"}, "messages": []}
    script = f"""
import json, sys
sys.stdin.readline()
car = {car!r}
print(json.dumps({{"ready": True, "car": car, "dbc": {{**car["dbc"], "messages": []}}}}), flush=True)
sys.stdin.read()
"""
    manager = Manager(child_command=(sys.executable, "-c", script))
    assert manager.status()["car"] is None
    run = await manager.start(default_profile_path().with_name("car_toyota_demo.yaml").read_text())
    assert run["car"] is None
    try:
        await until(manager, "Running")
        status = manager.status()
        assert status["car"] == car
        assert status["dbc"]["sha256"] == "ab" * 32
    finally:
        await manager.close()
    assert manager.status()["car"] is None


@pytest.mark.asyncio
async def test_a_large_car_ready_event_is_read_whole():
    # About 220 bytes of metadata per message: a 1000-message bus is over asyncio's default 64 KiB line limit.
    script = """
import json, sys
sys.stdin.readline()
messages = [{"name": f"MSG_{i:04d}", "skipped": None, "pad": "x" * 200} for i in range(1000)]
print(json.dumps({"ready": True, "car": {"messages": messages}}), flush=True)
sys.stdin.read()
"""
    manager = Manager(child_command=(sys.executable, "-c", script))
    await manager.start(default_profile_path().with_name("car_toyota_demo.yaml").read_text())
    try:
        await until(manager, "Running")
        assert len(manager.status()["car"]["messages"]) == 1000
    finally:
        await manager.close()
