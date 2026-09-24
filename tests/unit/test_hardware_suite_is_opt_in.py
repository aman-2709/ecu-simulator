"""tests/hardware must be invisible to an ordinary run and must refuse an unsafe bench.

A marker cannot do this job. pytest collects first and deselects after, so a default run
would import the suite -- and with it pyserial, which the default install deliberately
does not have. These tests drive pytest in a subprocess so the real collection behaviour
is exercised, not a simulation of it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]

BENCH_ENV = {
    "ECU_SIM_HW_BENCH": "1",
    "ECU_SIM_HW_CAN_IFACE": "can0",
    "ECU_SIM_HW_SERIAL": "/dev/rfcomm0",
}


def collect(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("ECU_SIM_HW_")}
    environment.update(env or {})
    return subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "pytest", "--collect-only", "-q", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_a_default_run_does_not_collect_the_hardware_suite():
    # Assert on collected node ids rather than raw output: a warning that merely mentions
    # the path is not a collection, and matching bare text would confuse the two.
    result = collect()
    collected = [line for line in result.stdout.splitlines() if "::" in line]
    assert not [line for line in collected if line.startswith("tests/hardware")], collected[-20:]


def test_a_default_run_still_collects_the_ordinary_suites():
    # Narrowing testpaths must not accidentally drop a suite.
    result = collect()
    for suite in ("tests/unit", "tests/characterization", "tests/integration"):
        assert suite in result.stdout, f"{suite} missing from default collection"


def test_asking_for_the_suite_by_path_still_refuses_without_opt_in():
    result = collect("tests/hardware")
    assert "tests/hardware/test_" not in result.stdout
    assert "ECU_SIM_HW_BENCH" in result.stdout + result.stderr


def test_opting_in_without_naming_the_interface_refuses():
    result = collect("tests/hardware", env={"ECU_SIM_HW_BENCH": "1"})
    assert "ECU_SIM_HW_CAN_IFACE" in result.stdout + result.stderr


def test_opting_in_without_naming_the_serial_device_refuses():
    result = collect(
        "tests/hardware",
        env={"ECU_SIM_HW_BENCH": "1", "ECU_SIM_HW_CAN_IFACE": "can0"},
    )
    assert "ECU_SIM_HW_SERIAL" in result.stdout + result.stderr


def test_a_virtual_interface_is_refused():
    # A hardware suite pointed at vcan would pass while proving nothing, and would produce
    # "hardware validated" evidence for bytes that never reached a wire. The simulated
    # backend has its own home in tests/integration and does not come through here.
    result = collect("tests/hardware", env={**BENCH_ENV, "ECU_SIM_HW_CAN_IFACE": "vcan0"})
    assert "virtual" in (result.stdout + result.stderr).lower()


def test_a_fully_named_bench_is_collected():
    # With the bench named, collection proceeds. Whether the devices actually exist is a
    # runtime skip in the fixture, not a collection refusal -- the difference matters
    # because "no bench attached" must report as skipped-with-reason, not as nothing found.
    result = collect("tests/hardware", env=BENCH_ENV)
    assert "tests/hardware/test_" in result.stdout, result.stdout[-2000:]


def test_the_hardware_marker_is_applied_automatically():
    result = collect("tests/hardware", "-m", "hardware", env=BENCH_ENV)
    assert "tests/hardware/test_" in result.stdout, result.stdout[-2000:]
