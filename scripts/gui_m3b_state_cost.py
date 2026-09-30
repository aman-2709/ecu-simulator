#!/usr/bin/env python3
"""GUI M3b checkpoint-1 overhead measurement (design §17, task 1.5).

State build and encode cost, in-process only: no socket, no vcan, no transport, and no
publisher turn. It builds a runtime from the shipped ``ice_scenario.yaml`` profile on
interface ``vcan0`` (``app.RuntimeConfig.build`` / ``app.build_runtime``, as
``scripts/gui_m1_early_check.py`` does), applies the scenario once so ``as_of`` is set,
then times 10,000 calls to ``snapshots.state_message`` end to end, and separately times
the build (``vehicle`` + ``dtcs``) and the JSON encode alone, so build and encode costs
are distinguishable.

It runs unchanged against the code before and after checkpoint 1: ``vehicle(runtime,
unavailable)``, ``dtcs(runtime)`` and ``state_message(runtime, unavailable)`` have the
same signatures on both sides. ``check_state_size`` is not used -- its signature changed
at checkpoint 1.

This is not a publisher turn (that is ``scripts/gui_m1_early_check.py``'s
``longest_turn_s``), not the M2 early check, and not the M4 benchmark.
"""

from __future__ import annotations

import json
import statistics
import sys
import time

import ecu_simulator
from ecu_simulator import app
from ecu_simulator.cli import default_profile_path
from ecu_simulator.config import load_profile
from ecu_simulator.observe import availability, snapshots

N = 10_000
WARMUP = 200
SCENARIO_T = 1.0


def timed(fn, n: int = N) -> list[float]:
    """``n`` timed no-argument calls to ``fn``, in microseconds, sorted ascending."""
    samples = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - t0) / 1000)
    return sorted(samples)


def report(label: str, samples: list[float]) -> tuple[float, float, float]:
    med = statistics.median(samples)
    p99 = samples[int(len(samples) * 0.99)]
    mx = samples[-1]
    print(f"{label}: median {med:.3f} us  p99 {p99:.3f} us  max {mx:.3f} us  n={len(samples)}")
    return med, p99, mx


def main() -> int:
    print(f"ecu_simulator package: {ecu_simulator.__file__}")

    profile_path = default_profile_path().with_name("ice_scenario.yaml")
    print(f"profile: {profile_path}")
    config = app.RuntimeConfig.build(load_profile(profile_path), "vcan0")
    runtime = app.build_runtime(config)

    unavail = availability.unavailable(runtime.config.profile)

    # Apply the scenario once so as_of is set, the way a first request or the periodic
    # tick would -- no socket is opened here, so nothing else does it for us.
    if runtime.runner is not None:
        runtime.runner.apply(SCENARIO_T)

    for _ in range(WARMUP):
        snapshots.state_message(runtime, unavail)

    e2e = timed(lambda: snapshots.state_message(runtime, unavail))
    report("state_message (end-to-end, build+encode)", e2e)

    def build() -> None:
        snapshots.vehicle(runtime, unavail)
        snapshots.dtcs(runtime)

    for _ in range(WARMUP):
        build()
    build_samples = timed(build)
    report("build alone (vehicle + dtcs)", build_samples)

    # Encode alone: build the payload once, outside the timing loop, and re-encode the
    # same object every iteration -- json.dumps does not mutate it. The after code always
    # encodes state_message with allow_nan=False (design §8.3, §14.1 C11); the before code
    # encodes with the default allow_nan=True. Both flag values are timed here so the
    # encode-alone cost of the flag itself is visible; state_message above is the
    # authoritative before/after number, because it calls encode the way each side's own
    # code actually does.
    payload = {"type": "state", "vehicle": snapshots.vehicle(runtime, unavail), "dtcs": snapshots.dtcs(runtime)}

    for _ in range(WARMUP):
        json.dumps(payload, separators=(",", ":"), allow_nan=False)
    encode_strict = timed(lambda: json.dumps(payload, separators=(",", ":"), allow_nan=False))
    report("encode alone (allow_nan=False)", encode_strict)

    for _ in range(WARMUP):
        json.dumps(payload, separators=(",", ":"), allow_nan=True)
    encode_lenient = timed(lambda: json.dumps(payload, separators=(",", ":"), allow_nan=True))
    report("encode alone (allow_nan=True)", encode_lenient)

    return 0


if __name__ == "__main__":
    sys.exit(main())
