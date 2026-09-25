"""The acceptance cases, run against a real adapter on the bench.

This is the **physical** backend, and the only one that can produce Phase 8b evidence. It
is opt-in, never runs in CI, and refuses a virtual interface -- see this package's conftest.

The suite starts the simulator itself, on the named interface, and only after proving that
nothing on the bus answers OBD yet (``bench_simulator`` in the conftest). **Do not start a
simulator by hand**: the check would find it and refuse the bench. Configure the link once,
then run:

    sudo scripts/setup_can.sh can0 500000

    ECU_SIM_HW_BENCH=1 \\
    ECU_SIM_HW_CAN_IFACE=can0 \\
    ECU_SIM_HW_SERIAL=/dev/ttyUSB0 \\
    pytest tests/hardware -v

A case marked ``mutates`` changes ECU state. Those run last, and the simulator is restarted
after each, with the bus checked again before it comes back up.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "serial", reason="needs the optional [hardware] extra: pip install -e '.[dev,hardware]'"
)

from tests.hardware.acceptance_cases import cases_for  # noqa: E402
from tests.hardware.bench_simulator import BenchSimulator  # noqa: E402
from tests.hardware.conftest import HardwareBench  # noqa: E402
from tests.hardware.elm327_serial import Elm327  # noqa: E402
from tests.hardware.tester import PHYSICAL, AcceptanceCase  # noqa: E402

CASES = cases_for(PHYSICAL)


@pytest.fixture(scope="module")
def tester(hw_bench: HardwareBench):
    device = Elm327(hw_bench.serial_port, baud=hw_bench.baud, timeout=10.0)
    try:
        yield device
    finally:
        device.close()


def test_the_registry_is_not_empty():
    assert CASES, "no acceptance cases apply to the physical backend"


@pytest.mark.parametrize("case", CASES, ids=str)
def test_acceptance_case(case: AcceptanceCase, tester, bench_simulator: BenchSimulator):
    case.run(tester)
    if case.mutates:
        # The case changed ECU state a later one would read. Restore a known simulator.
        bench_simulator.restart()
