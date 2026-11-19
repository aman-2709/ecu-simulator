#!/usr/bin/env python3
"""GUI M1 early check (decisions/0010 §9.2).

Only an in-process early check: no network, no vcan, no transport. It is not the M2 early
check and not the M4 benchmark, and its figures say nothing about wire latency.

1. Dispatch overhead: 20,000 calls through a bare Dispatcher against the same calls through
   ObservedDispatcher with the Publisher draining between calls.
2. Loop hold: drain a full 4096-record HandOff and report the longest publisher turn.
Stop rule: median overhead > 10 µs, p99 overhead > 50 µs, or any turn > 2 ms.
"""

from __future__ import annotations

import statistics
import sys
import time

from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.publisher import Publisher
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticRequest

N = 20_000
MIX = [DiagnosticRequest(b"\x01\x0d", 0x7DF, functional=True), DiagnosticRequest(b"\x01\x10", 0x7DF, functional=True)]


def timed(handler, publisher=None) -> list[float]:
    samples = []
    for i in range(N):
        request = MIX[i % 2] if i % 100 else DiagnosticRequest(b"\x09\x02", 0x7DF, functional=True)
        t0 = time.perf_counter_ns()
        handler(request)
        samples.append((time.perf_counter_ns() - t0) / 1000)
        if publisher is not None:
            while publisher.drain_turn():
                pass
    return sorted(samples)


def main() -> int:
    config = app.RuntimeConfig.build(load_profile(default_profile_path()), "vcan0")
    bare = timed(app.build_runtime(config).dispatcher)
    runtime = app.build_runtime(config)
    endpoints = {e.name: e for e in app.build_endpoints(config)}
    handoff = HandOff()
    publisher = Publisher(handoff, runtime.router, endpoints)
    wrapped = timed(ObservedDispatcher(runtime.dispatcher, handoff, lambda: None), publisher)
    med = statistics.median(wrapped) - statistics.median(bare)
    p99 = wrapped[int(N * 0.99)] - bare[int(N * 0.99)]

    full = HandOff()
    p2 = Publisher(full, runtime.router, endpoints)
    fill = ObservedDispatcher(runtime.dispatcher, full, lambda: None)
    while len(full) < 4096:
        fill(MIX[0])
    turns = 0
    while len(full):
        p2.drain_turn()
        turns += 1
    print(f"bare median {statistics.median(bare):.2f} us  p99 {bare[int(N*0.99)]:.2f} us")
    print(f"wrapped median {statistics.median(wrapped):.2f} us  p99 {wrapped[int(N*0.99)]:.2f} us")
    print(f"overhead median {med:.2f} us  p99 {p99:.2f} us")
    print(f"full HandOff: {turns} turns, longest turn {p2.longest_turn_s*1e3:.3f} ms")
    stop = med > 10 or p99 > 50 or p2.longest_turn_s > 0.002
    print("STOP: report before M2" if stop else "within the M1 early-check limits")
    return 1 if stop else 0


if __name__ == "__main__":
    sys.exit(main())
