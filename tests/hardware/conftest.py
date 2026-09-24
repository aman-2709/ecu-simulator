"""Opt-in gate and bench isolation for the physical-hardware suite.

Everything collected under ``tests/hardware`` talks to a real adapter on a real bus. Three
separate refusals guard it, because they protect against three different mistakes:

1. **Collection is refused unless the bench is named explicitly.** Nothing here has a
   default interface or a default serial device. A default is how a suite ends up
   transmitting on whatever CAN interface happened to be up, and this suite transmits.
2. **A virtual interface is refused outright.** A hardware suite pointed at ``vcan`` would
   pass while proving nothing, and would produce "hardware validated" evidence for bytes
   that never reached a wire. The simulated backend has its own home in
   ``tests/integration`` and never comes through here.
3. **A bus that already has a responder on it is refused at runtime.** On a dedicated
   bench nothing answers OBD before the simulator starts. On a vehicle bus a real ECU
   would. That is what turns the ruling in ``docs/decisions/0008`` section 2 -- this
   simulator never transmits on a live vehicle bus -- from a sentence in a document into a
   refusal in code.

The marker alone cannot do any of this: pytest collects before it deselects, so a default
run would import this package and, with it, pyserial.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from ecu_simulator.transport.socketcan import interface as iface_mod
from tests.integration.conftest import _foreign_responder_present

BENCH_FLAG = "ECU_SIM_HW_BENCH"
IFACE_VAR = "ECU_SIM_HW_CAN_IFACE"
SERIAL_VAR = "ECU_SIM_HW_SERIAL"
BAUD_VAR = "ECU_SIM_HW_BAUD"

# ELM327DSJ page 8: 38400 baud unless PP 0C was changed, or 9600 if pin 6 was low at
# power up. Configurable because both are real, and an OBDLink LX over rfcomm may differ
# again -- the value is recorded in the bench report rather than assumed.
DEFAULT_BAUD = 38400


@dataclass(frozen=True)
class HardwareBench:
    """The bench as the operator named it. Recorded verbatim in the Phase 8b report."""

    can_iface: str
    serial_port: str
    baud: int


def _refusal() -> str | None:
    """Why this suite must not be collected, or None when the bench is fully named."""
    if os.environ.get(BENCH_FLAG) != "1":
        return (
            f"the physical-hardware suite is opt-in: set {BENCH_FLAG}=1 to acknowledge that it "
            "transmits on a real CAN bus, and never point it at a vehicle"
        )
    iface = os.environ.get(IFACE_VAR)
    if not iface:
        return f"{IFACE_VAR} is not set: name the bench CAN interface explicitly (there is no default)"
    if iface.startswith("vcan"):
        return (
            f"{IFACE_VAR}={iface!r} is a virtual interface. This suite exists to show the bytes "
            "survive real wire, and vcan would pass without proving it. The simulated backend "
            "runs from tests/integration instead."
        )
    if not os.environ.get(SERIAL_VAR):
        return (
            f"{SERIAL_VAR} is not set: name the adapter's serial device, e.g. /dev/rfcomm0 for a "
            "Bluetooth adapter bound with rfcomm, or /dev/ttyUSB0 for USB"
        )
    return None


def pytest_ignore_collect(collection_path, config):  # noqa: ARG001
    return True if _refusal() is not None else None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/hardware" in str(item.fspath):
            item.add_marker(pytest.mark.hardware)


def pytest_configure(config: pytest.Config) -> None:
    reason = _refusal()
    if reason and any("hardware" in str(arg) for arg in config.args):
        # Asked for by name but not opted in: say why, rather than reporting "no tests ran".
        print(f"\ntests/hardware not collected: {reason}\n")


@pytest.fixture(scope="session")
def hw_bench() -> HardwareBench:
    """The named bench, once it has been proved present, up and unoccupied."""
    reason = _refusal()
    if reason:
        pytest.skip(reason)
    iface = os.environ[IFACE_VAR]
    try:
        iface_mod.interface_index(iface)
    except Exception:
        pytest.skip(f"CAN interface {iface!r} does not exist: run scripts/setup_can.sh {iface} 500000")
    if not iface_mod.is_interface_up(iface):
        pytest.skip(f"CAN interface {iface!r} is down: sudo scripts/setup_can.sh {iface} 500000")
    if _foreign_responder_present(iface):
        pytest.fail(
            f"something already answers OBD requests on {iface!r}. This suite must run on a "
            "dedicated bench carrying nothing but the simulator and the tester. If this is a "
            "vehicle, stop: docs/decisions/0008 rules that this simulator never transmits on a "
            "live vehicle bus."
        )
    return HardwareBench(
        can_iface=iface,
        serial_port=os.environ[SERIAL_VAR],
        baud=int(os.environ.get(BAUD_VAR, DEFAULT_BAUD)),
    )
