"""decisions/0010 §9.1, the two proofs M1 deferred: with the API off, run() hands the
transport the plain Dispatcher, and aiohttp is never imported."""

import asyncio
import subprocess
import sys

import pytest

from ecu_simulator import app, cli
from ecu_simulator.api.options import ApiOptions, ApiStartupError
from ecu_simulator.config import load_profile
from ecu_simulator.ecu.dispatcher import Dispatcher
from tests.unit.test_cli import isolated_logging  # noqa: F401  (fixture: cli.main configures logging)


def shipped():
    return app.RuntimeConfig.build(load_profile(cli.default_profile_path()), "vcan0")


class Capture:
    handlers: list = []
    instances: list = []

    def __init__(self, interface, endpoints):
        Capture.instances.append(self)

    async def start(self, handler):
        Capture.handlers.append(handler)

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_api_off_hands_the_transport_the_plain_dispatcher():
    Capture.handlers.clear()
    stop = asyncio.Event()
    stop.set()
    await app.run(shipped(), stop=stop, install_signal_handlers=False, transport_factory=Capture)
    (handler,) = Capture.handlers
    assert type(handler) is Dispatcher


def test_api_off_never_imports_aiohttp():
    program = (
        "import asyncio, sys\n"
        "from ecu_simulator import app, cli\n"
        "from ecu_simulator.config import load_profile\n"
        "class T:\n"
        "    def __init__(self, i, e): pass\n"
        "    async def start(self, h): pass\n"
        "    async def stop(self): pass\n"
        "async def go():\n"
        "    stop = asyncio.Event(); stop.set()\n"
        "    config = app.RuntimeConfig.build(load_profile(cli.default_profile_path()), 'vcan0')\n"
        "    await app.run(config, stop=stop, install_signal_handlers=False, transport_factory=T)\n"
        "asyncio.run(go())\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'aiohttp'))\n"
    )
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_the_cli_refuses_a_non_loopback_api_with_exit_2(isolated_logging, monkeypatch):  # noqa: F811
    called = []
    monkeypatch.setattr(app, "main", lambda *a, **k: called.append(1) or 0)
    assert cli.main(["--api", "0.0.0.0:8765"]) == 2 and called == []


def test_the_cli_refuses_api_without_the_extra_with_exit_2(isolated_logging, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None if name == "aiohttp" else object())
    called = []
    monkeypatch.setattr(app, "main", lambda *a, **k: called.append(1) or 0)
    assert cli.main(["--api", "127.0.0.1:8765"]) == 2 and called == []


def test_the_cli_passes_parsed_options_to_app_main(isolated_logging, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())
    seen = []
    monkeypatch.setattr(app, "main", lambda config, api=None: seen.append(api) or 0)
    assert cli.main(["--api", "localhost:8765"]) == 0
    (options,) = seen
    assert (options.host, options.port, options.profile) == ("127.0.0.1", 8765, str(cli.default_profile_path()))


def test_app_main_turns_an_api_startup_error_into_exit_2(monkeypatch):
    async def failing(config, api=None):
        raise ApiStartupError("busy")
    monkeypatch.setattr(app, "run", failing)
    assert app.main(shipped(), ApiOptions("127.0.0.1", 1, "p", "t")) == 2
