#!/usr/bin/env python3
"""Phase 7 scenario acceptance: drive ice_scenario.yaml on a real bus and check the bytes.

**Status.** This is the official Phase 7 acceptance script for this repository, so
designated by the reviewer on 2026-09-22.

**Provenance, which the status does not change.** It was written in the documentation
session of 2026-09-22 to make the manual Phase 7 acceptance reproducible. It is *not* the
script the reviewer ran by hand; that one was not kept, and no attempt has been made to
reconstruct it. This script checks the same behaviors at the same checkpoints and was run
independently: 23 checks, all passing, against a reviewer run of 21 checks, all passing.
docs/validation/phase-7-acceptance.md reports the two as two runs that agree, never as
one, and nothing here is presented as the reviewer's evidence.

It runs in real time against a real ISO-TP socket, so the whole run takes just over two
minutes. That is the point: the unit suite already proves the scenario logic against a
SimulatedClock, and what this adds is that a wall-clock run through the kernel produces
the same bytes.

Usage, from the repository root:

    scripts/acceptance/run_phase7_acceptance.sh          # private namespace, no root
    python scripts/acceptance/phase7_scenario_acceptance.py --interface vcan0

Exit status is 0 only if every check passed and the simulator exited cleanly.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import isotp

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "src" / "ecu_simulator" / "profiles" / "ice_scenario.yaml"
READY_MARKER = "ecu-simulator ready on"

OBD_PHYSICAL = (0x7E0, 0x7E8)  # tester tx, tester rx
UDS_PHYSICAL = (0x7E1, 0x7E9)


@dataclass
class Check:
    """One request at one checkpoint, and what it must answer."""

    at: float  # seconds after the simulator became ready
    request: str  # hex
    expect: str | None  # hex, or None for "no response"
    why: str
    channel: str = "obd"
    # A value on a ramp moves continuously, so an exact byte would depend on arriving at
    # the instant to the millisecond. Where that is true the check names the byte it wants
    # and how far either way is still a pass; everything else is exact.
    tolerance: int = 0
    tolerance_index: int = -1

    observed: str | None = field(default=None, init=False)
    passed: bool | None = field(default=None, init=False)
    at_actual: float = field(default=0.0, init=False)

    @property
    def exact(self) -> bool:
        return self.tolerance == 0

    def evaluate(self, observed: str | None) -> bool:
        self.observed = observed
        if self.exact or observed is None or self.expect is None:
            self.passed = observed == self.expect
            return self.passed
        want, got = bytes.fromhex(self.expect), bytes.fromhex(observed)
        if len(want) != len(got):
            self.passed = False
            return False
        i = self.tolerance_index % len(want)
        self.passed = (
            want[:i] == got[:i] and want[i + 1 :] == got[i + 1 :] and abs(want[i] - got[i]) <= self.tolerance
        )
        return self.passed


# The checkpoints the manual acceptance used, with the expected bytes derived from
# ice_scenario.yaml and confirmed against the implementation before this script was run.
#
# Read the timeline generators as the implementation defines them: the value of the latest
# point whose `at` has arrived, held until the next one. They do **not** interpolate. At
# t=25 the speed is therefore 30 km/h, the value of the point at t=20, and not the 42.5
# that interpolating towards the t=30 point would give. That distinction is what the
# corrected manual script checks, and checks 3 and 4 below pin it.
CHECKS: list[Check] = [
    # t = 1 s: stationary, nothing faulted.
    Check(1, "010d", "410d00", "speed 0 km/h: timeline point at t=0"),
    Check(1, "03", "4300", "no confirmed trouble codes"),
    Check(1, "1902ff", "59028c", "no trouble codes in the UDS view either", channel="uds"),
    # t = 25 s: inside the timeline segment that starts at t=20. Stepwise, not interpolated.
    Check(25, "010d", "410d1e", "speed 30 km/h: the t=20 point held, NOT interpolated to 42.5"),
    Check(25, "010c", "410c1900", "engine speed 1600 rpm: the t=20 point held"),
    Check(25, "0105", "41055a", "coolant 50 C: the ramp 20->92 over 60 s, halfway less a bit", tolerance=2),
    Check(25, "010d", "410d1e", "reading speed again does not advance the scenario"),
    # t = 35 s: still before the first event at t=40.
    Check(35, "03", "4300", "no confirmed trouble codes yet"),
    Check(35, "1902ff", "59028c", "and none pending: the first event is at t=40", channel="uds"),
    # t = 45 s: after raise_pending at t=40, before raise_confirmed at t=75.
    Check(45, "1902ff", "59028c01280104", "P0128 pending, status 0x04", channel="uds"),
    Check(45, "03", "4300", "pending is not what service 03 reports"),
    # t = 78 s: after raise_confirmed and request_indicator, both at t=75.
    Check(78, "03", "43010128", "P0128 now confirmed and reported by service 03"),
    Check(78, "1902ff", "59028c0128018c", "status 0x8C: pending, confirmed, indicator", channel="uds"),
    Check(78, "0105", "410584", "coolant 92 C: the ramp finished at t=60 and holds"),
    # t = 82 s: a tester clears the fault over OBD, and both views must agree.
    Check(82, "04", "44", "Mode 04 acknowledged"),
    Check(82, "03", "4300", "cleared in the OBD view"),
    Check(82, "1902ff", "59028c", "cleared in the UDS view by the same operation", channel="uds"),
    Check(82, "03", "4300", "reading again leaves it cleared"),
    # t = 125 s: past the end of the drive. The consumed event must not be replayed.
    Check(125, "03", "4300", "the fault has not come back"),
    Check(125, "1902ff", "59028c", "nor in the UDS view", channel="uds"),
    Check(125, "010d", "410d00", "stationary: the timeline point at t=120"),
    Check(125, "010c", "410c0c80", "engine speed back to 800 rpm idle"),
    Check(125, "0105", "410584", "coolant still 92 C"),
]


class Tester:
    """One ISO-TP tester socket, padded the way an ELM327 pads."""

    def __init__(self, interface: str, tx_id: int, rx_id: int, timeout: float = 2.0) -> None:
        self.sock = isotp.socket(timeout=timeout)
        self.sock.set_opts(optflag=isotp.socket.flags.TX_PADDING, txpad=0x00)
        self.sock.bind(interface, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=rx_id, txid=tx_id))

    def ask(self, request_hex: str) -> str | None:
        self.sock.send(bytes.fromhex(request_hex))
        try:
            return self.sock.recv().hex()
        except (TimeoutError, OSError):
            return None

    def close(self) -> None:
        self.sock.close()


class Simulator:
    def __init__(self, interface: str, profile: Path) -> None:
        self.lines: list[str] = []
        self._ready = threading.Event()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ecu_simulator",
                "--interface",
                interface,
                "--profile",
                str(profile),
                "--log-level",
                "INFO",
            ],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.lines.append(line)
            if READY_MARKER in line:
                self._ready.set()

    def wait_ready(self, timeout: float = 10.0) -> None:
        if not self._ready.wait(timeout):
            self.kill()
            raise RuntimeError("simulator did not become ready:\n" + "".join(self.lines))

    def interrupt(self, timeout: float = 5.0) -> int:
        self.proc.send_signal(signal.SIGINT)
        return self.proc.wait(timeout=timeout)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)

    @property
    def log(self) -> str:
        return "".join(self.lines)


def run(interface: str, trace_path: Path | None) -> int:
    if not PROFILE.is_file():
        print(f"profile not found: {PROFILE}", file=sys.stderr)
        return 2

    trace = None
    if trace_path is not None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace = subprocess.Popen(
            ["candump", "-L", interface], stdout=trace_path.open("w"), stderr=subprocess.DEVNULL
        )

    simulator = Simulator(interface, PROFILE)
    testers: dict[str, Tester] = {}
    try:
        simulator.wait_ready()
        started = time.monotonic()
        testers = {
            "obd": Tester(interface, *OBD_PHYSICAL),
            "uds": Tester(interface, *UDS_PHYSICAL),
        }
        print(f"simulator ready on {interface}; driving {PROFILE.name} for "
              f"{max(c.at for c in CHECKS):.0f}s\n", flush=True)

        for check in CHECKS:
            remaining = check.at - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
            check.at_actual = time.monotonic() - started
            check.evaluate(testers[check.channel].ask(check.request))
            mark = "PASS" if check.passed else "FAIL"
            band = f" (+/-{check.tolerance} on byte {check.tolerance_index})" if not check.exact else ""
            print(
                f"  [{mark}] t={check.at_actual:7.2f}s  {check.request:<8} -> "
                f"{str(check.observed):<16} expected {str(check.expect):<16}{band}  {check.why}",
                flush=True,
            )

        exit_code = simulator.interrupt()
    finally:
        for tester in testers.values():
            tester.close()
        simulator.kill()
        if trace is not None:
            trace.terminate()
            trace.wait(timeout=5)

    failed = [c for c in CHECKS if not c.passed]
    clean = exit_code == 0
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    print(f"simulator exit status {exit_code} ({'clean' if clean else 'NOT CLEAN'})")
    if trace_path is not None:
        print(f"CAN trace written to {trace_path}")
    if failed:
        print("\nfailed:")
        for c in failed:
            print(f"  t={c.at}s {c.request} -> {c.observed}, expected {c.expect} ({c.why})")
    return 0 if not failed and clean else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interface", default=os.environ.get("ECU_SIM_CAN_IFACE", "vcan0"))
    parser.add_argument("--trace", type=Path, default=None, help="write a candump -L trace here")
    parser.add_argument("--json", type=Path, default=None, help="write the results as JSON here")
    args = parser.parse_args(argv)

    status = run(args.interface, args.trace)

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                [
                    {
                        "at": c.at,
                        "at_actual": round(c.at_actual, 3),
                        "channel": c.channel,
                        "request": c.request,
                        "expect": c.expect,
                        "observed": c.observed,
                        "tolerance": c.tolerance,
                        "passed": c.passed,
                        "why": c.why,
                    }
                    for c in CHECKS
                ],
                indent=2,
            )
        )
    return status


if __name__ == "__main__":
    sys.exit(main())
