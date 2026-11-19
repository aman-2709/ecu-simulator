"""0010 §9.1: wrapping changes no reply. Two runtimes built from one profile are driven in
lockstep with the same request sequence, one bare and one wrapped. Every reply must be
byte-identical, including after the DTC-clearing requests, which mutate both sides alike.
"""

import itertools

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticRequest

ADDRESSES = ((0x7DF, True), (0x7E0, False), (0x7E1, False))


def requests():
    runtime = app.build_runtime(app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0"))
    sids = sorted({sid for ecu in runtime.ecus for protocol in ecu.protocols for sid in protocol.service_ids})
    for (address, functional), sid in itertools.product(ADDRESSES, sids):
        yield DiagnosticRequest(bytes([sid]), address, functional=functional)
        for second, length in itertools.product(range(256), range(2, 6)):
            yield DiagnosticRequest(bytes([sid, second]) + bytes(length - 2), address, functional=functional)


def test_wrapping_changes_no_reply():
    config = app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0")
    bare = app.build_runtime(config).dispatcher
    observed = ObservedDispatcher(app.build_runtime(config).dispatcher, HandOff(max_records=1), lambda: None)
    compared = 0
    for request in requests():
        a, b = bare(request), observed(request)
        assert (a.payload if a else None) == (b.payload if b else None), request
        compared += 1
    assert compared > 40_000  # "tens of thousands", 0010 §9.1
