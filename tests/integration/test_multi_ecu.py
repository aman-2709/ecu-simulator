"""Phase 9 multi-ECU fan-out through real vcan and kernel CAN_ISOTP sockets."""

from collections.abc import Iterator

import isotp
import pytest

from ecu_simulator.cli import default_profile_path
from tests.integration.conftest import FunctionalFanoutTester, Simulator


@pytest.fixture(scope="module")
def multi_simulator(vcan: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Simulator]:
    profile = default_profile_path().with_name("multi_ecu.yaml")
    sim = Simulator(vcan, str(tmp_path_factory.mktemp("multi-ecu")), profile=str(profile))
    try:
        sim.wait_ready()
        yield sim
    finally:
        sim.terminate()


def test_one_11bit_functional_request_is_answered_by_both_ecus(vcan, multi_simulator):
    tester = FunctionalFanoutTester(vcan, 0x7DF, {0x7E8: 0x7E0, 0x7E9: 0x7E1})
    try:
        tester.send(b"\x01\x0d")
        assert tester.recv(0x7E8) == b"\x41\x0d\x2a"
        assert tester.recv(0x7E9) == b"\x41\x0d\x2a"
    finally:
        tester.close()


def test_one_normal_fixed_29bit_functional_request_is_answered_by_both_ecus(vcan, multi_simulator):
    tester = FunctionalFanoutTester(
        vcan,
        0x18DB33F1,
        {0x18DAF110: 0x18DA10F1, 0x18DAF118: 0x18DA18F1},
        mode=isotp.AddressingMode.Normal_29bits,
    )
    try:
        tester.send(b"\x01\x0d")
        assert tester.recv(0x18DAF110) == b"\x41\x0d\x2a"
        assert tester.recv(0x18DAF118) == b"\x41\x0d\x2a"
    finally:
        tester.close()
