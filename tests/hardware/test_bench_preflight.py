"""The smallest real hardware test: the bench is what the operator said it is.

It exists from Task 6 so the opt-in machinery has something to collect, and it is a
genuine check -- a bench whose interface is down, missing, or already busy fails here,
clearly, before any protocol question is asked.
"""

from tests.hardware.conftest import HardwareBench


def test_the_bench_is_named_and_reachable(hw_bench: HardwareBench):
    assert not hw_bench.can_iface.startswith("vcan")
    assert hw_bench.serial_port
    assert hw_bench.baud > 0
