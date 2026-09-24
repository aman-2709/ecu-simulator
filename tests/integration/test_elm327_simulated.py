"""The acceptance cases, run against a fake ELM327 bridged to vcan.

This is the **simulated** backend. It is a regression test for this project's own Python
ELM327 harness -- elm327_parser, Elm327 and the acceptance cases themselves -- exercised
end to end on every ordinary run, with no hardware.

What a pass here means: the harness and the simulator agree. It is **not** hardware
evidence and closes no item in 0007 section 6.2; those need the bench and the physical
backend in tests/hardware/test_elm327_physical.py. Real-hardware interoperability was
separately demonstrated on 2026-09-23 (docs/validation/phase-8-smoke-test/).

Needs a vcan interface and the optional [hardware] extra, and skips with a reason without
either. CI has the extra deliberately absent and, on GitHub-hosted runners, no
CONFIG_CAN_ISOTP either -- so this file does not execute there. See the Task 12 notes in
docs/plans/phase-8a-implementation.md.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "serial", reason="needs the optional [hardware] extra: pip install -e '.[dev,hardware]'"
)

from tests.hardware.acceptance_cases import cases_for  # noqa: E402
from tests.hardware.bridged_elm327 import BridgedElm327  # noqa: E402
from tests.hardware.elm327_serial import Elm327  # noqa: E402
from tests.hardware.tester import SIMULATED, AcceptanceCase  # noqa: E402
from tests.integration.conftest import Simulator  # noqa: E402

CASES = cases_for(SIMULATED)


@pytest.fixture
def bridged(vcan, simulator: Simulator):
    device = BridgedElm327(vcan)
    device.start()
    try:
        yield device
    finally:
        device.stop()


@pytest.fixture
def tester(bridged):
    device = Elm327(bridged.port, timeout=5.0)
    try:
        yield device
    finally:
        device.close()


def test_the_registry_is_not_empty():
    # A backend that silently ran zero cases would look exactly like a passing suite.
    assert CASES, "no acceptance cases apply to the simulated backend"


@pytest.mark.parametrize("case", CASES, ids=str)
def test_acceptance_case(case: AcceptanceCase, tester, simulator: Simulator):
    case.run(tester)
    if case.mutates:
        # The case changed ECU state a later one would read. Restore a known simulator.
        simulator.restart()
