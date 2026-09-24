"""pyserial is an opt-in extra and nothing in the default install may need it.

This is the first new dependency since Phase 4, and the whole case for adding it rests on
it being invisible unless somebody asks for it. These tests hold that line, and they hold
it whether or not the developer running them happens to have the extra installed -- which
is why none of them asserts that ``import serial`` fails.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text())
SRC = REPO / "src" / "ecu_simulator"


def test_the_hardware_extra_exists_and_pins_pyserial():
    extras = PYPROJECT["project"]["optional-dependencies"]
    assert "hardware" in extras, list(extras)
    assert any(spec.startswith("pyserial") for spec in extras["hardware"]), extras["hardware"]


def test_pyserial_is_pinned_to_a_major_version():
    # Same discipline as the runtime dependencies: an unpinned major is how a silent
    # breaking change arrives on a bench day.
    (spec,) = [s for s in PYPROJECT["project"]["optional-dependencies"]["hardware"] if "pyserial" in s]
    assert ">=" in spec and "<" in spec, spec


def test_pyserial_is_not_a_runtime_dependency():
    for spec in PYPROJECT["project"]["dependencies"]:
        assert "pyserial" not in spec, spec


def test_pyserial_is_not_in_the_dev_extra():
    # CI installs [dev]. If pyserial leaked in there, the suite would silently start
    # depending on it and the "never in CI" property would rot.
    for spec in PYPROJECT["project"]["optional-dependencies"]["dev"]:
        assert "pyserial" not in spec, spec


def test_no_production_module_imports_pyserial():
    offenders = [
        str(path.relative_to(REPO))
        for path in SRC.rglob("*.py")
        if "import serial" in path.read_text()
    ]
    assert offenders == [], offenders


def test_the_simulator_imports_and_validates_with_pyserial_unavailable():
    # The real guarantee: block `serial` at import time and prove the ordinary CLI path
    # still works. A plain import check would pass even if a lazy import lurked on the
    # startup path.
    program = (
        "import sys\n"
        "class Blocker:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name == 'serial' or name.startswith('serial.'):\n"
        "            raise ImportError('pyserial blocked for this test')\n"
        "        return None\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'serial' or name.startswith('serial.'):\n"
        "            raise ImportError('pyserial blocked for this test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "try:\n"
        "    import serial\n"
        "except ImportError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('blocker did not work')\n"
        "from ecu_simulator.cli import main\n"
        "raise SystemExit(main(['validate-config']))\n"
    )
    result = subprocess.run([sys.executable, "-c", program], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_the_hardware_harness_module_that_parses_responses_needs_no_pyserial():
    # tests/hardware/tester.py is imported by unit tests that run in CI, where the extra
    # is not installed. It must stay pure.
    path = REPO / "tests" / "hardware" / "tester.py"
    assert "import serial" not in path.read_text()
