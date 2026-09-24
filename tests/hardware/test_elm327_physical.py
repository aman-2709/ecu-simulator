"""The acceptance cases, run against a real adapter on the bench.

This is the **physical** backend, and the only one that can produce Phase 8b evidence. It
is opt-in, never runs in CI, and refuses a virtual interface -- see this package's conftest.

The simulator is not started here. The operator runs it against the bench interface, the
same way a real tester would meet it:

    sudo scripts/setup_can.sh can0 500000
    python -m ecu_simulator --interface can0

then, in another shell:

    ECU_SIM_HW_BENCH=1 \\
    ECU_SIM_HW_CAN_IFACE=can0 \\
    ECU_SIM_HW_SERIAL=/dev/rfcomm0 \\
    pytest tests/hardware -v

A case marked ``mutates`` changes ECU state. Those run last, and after them the simulator
must be restarted before the suite is run again -- this backend cannot restart it, because
it does not own it.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "serial", reason="needs the optional [hardware] extra: pip install -e '.[dev,hardware]'"
)

from tests.hardware.acceptance_cases import cases_for  # noqa: E402
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
def test_acceptance_case(case: AcceptanceCase, tester):
    case.run(tester)
