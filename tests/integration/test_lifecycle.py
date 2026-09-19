"""Startup and shutdown of the simulator process on the real kernel ISO-TP path."""

import asyncio
import os
import signal

import pytest

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from tests.integration.conftest import Simulator


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM], ids=["SIGINT", "SIGTERM"])
def test_signal_shuts_down_cleanly(vcan, tmp_path, sig):
    sim = Simulator(vcan, str(tmp_path))
    try:
        sim.wait_ready()
        sim.signal(sig)
        code, err = sim.wait(timeout=3.0)
    finally:
        sim.terminate()
    assert code == 0, err
    assert f"received {sig.name}, shutting down" in err
    assert "shutdown complete" in err
    assert "Traceback" not in err


def test_missing_interface_fails_fast_with_exit_code_2(vcan, tmp_path):
    # Needs a kernel with CAN_ISOTP (the vcan fixture guarantees it) so that the
    # interface check, not the ISO-TP support check, is what fails.
    sim = Simulator("nosuchcan9", str(tmp_path))
    code, err = sim.wait(timeout=5.0)
    assert code == 2
    assert "nosuchcan9" in err and "setup_vcan.sh" in err


@pytest.mark.asyncio
async def test_in_process_run_leaks_no_file_descriptors(vcan):
    config = app.RuntimeConfig.build(load_profile(default_profile_path()), vcan)
    before = len(os.listdir("/proc/self/fd"))
    stop = asyncio.Event()

    async def trigger():
        await asyncio.sleep(0.05)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await app.run(config, stop=stop, install_signal_handlers=False)
    assert len(os.listdir("/proc/self/fd")) == before
