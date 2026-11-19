"""aiohttp is an opt-in extra (decisions/0010 §4.1, §9.1). These tests hold whether or not
the developer running them has the extra installed, except the one that says so by name."""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import tomllib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text())
SRC = REPO / "src" / "ecu_simulator"
BLOCK_AIOHTTP = (
    "import sys\n"
    "class Block:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name == 'aiohttp' or name.startswith('aiohttp.'):\n"
    "            raise ImportError('aiohttp blocked by the test')\n"
    "        return None\n"
    "sys.meta_path.insert(0, Block())\n"
)


def test_the_gui_extra_pins_aiohttp_to_a_major_version():
    (spec,) = PYPROJECT["project"]["optional-dependencies"]["gui"]
    assert spec.startswith("aiohttp") and ">=" in spec and "<4" in spec, spec


def test_aiohttp_is_neither_a_runtime_nor_a_dev_dependency():
    for spec in PYPROJECT["project"]["dependencies"] + PYPROJECT["project"]["optional-dependencies"]["dev"]:
        assert "aiohttp" not in spec, spec


def test_only_the_server_module_imports_aiohttp():
    offenders = [
        str(path.relative_to(SRC)) for path in SRC.rglob("*.py")
        if ("import aiohttp" in path.read_text() or "from aiohttp" in path.read_text())
        and path != SRC / "api" / "server.py"
    ]
    assert offenders == [], offenders


def test_the_simulator_validates_a_profile_with_aiohttp_unavailable():
    program = BLOCK_AIOHTTP + "from ecu_simulator import cli\nraise SystemExit(cli.main(['validate-config']))\n"
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_aiohttp_is_not_installed_in_this_environment():
    # 0010 §9.1, the stronger "not installed" form. It runs in the .[dev] CI jobs, and skips
    # by name wherever the [gui] extra is installed.
    if importlib.util.find_spec("aiohttp") is not None:
        pytest.skip("aiohttp is installed (a [gui] environment): the 'not installed' form runs in the .[dev] jobs")
    assert importlib.util.find_spec("aiohttp") is None
