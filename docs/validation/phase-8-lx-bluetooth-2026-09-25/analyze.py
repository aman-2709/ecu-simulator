#!/usr/bin/env python3
"""Reproduce every figure in this directory's README from the committed evidence.

    python3 docs/validation/phase-8-lx-bluetooth-2026-09-25/analyze.py

Standard library only. Reads ``candump-full.log.gz`` and ``simulator-debug.log.gz`` from
the directory this script lives in and prints the figures the README reports.

Pairing rule: every request on 0x7DF is single-frame and the tester sent the next one only
after the previous answer (or its own timeout), so each request is paired with the 0x7E8
traffic that follows it up to the next request. A multi-frame answer is reassembled from
its first and consecutive frames. Latency is request frame to the **first** reply frame.
"""

from __future__ import annotations

import gzip
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("America/Los_Angeles")
FRAME = re.compile(r"\s*\((\d+\.\d+)\)\s+can0\s+([0-9A-F]+)\s+\[(\d)\]\s+(.*)")

# Expected bytes, copied from tests/hardware/acceptance_cases.py at 68859fa.
EXPECTED = {
    "0100": "41001e3f8013",
    "0120": "412000020001",
    "0140": "414044008000",
    "010c": "410c0c80",
    "010d": "410d00",
    "0105": "410582",
    "012f": "412f7f",
    "010b": "410b21",
    "0902": (b"\x49\x02\x01TESTVIN0123456789").hex(),
    "04": "44",
}
# Answers outside the acceptance cases, checked against the repository's own tests.
OTHER_REFERENCE = {
    "0110": ("4110015e", "tests/unit/test_obd_pids.py: engine.maf 3.5 -> 015e"),
    "090a": (
        (bytes.fromhex("490a") + bytes(7) + b"ECU_SIMULATOR").hex(),
        "tests/characterization/test_mode09_pid0a_frozen.py (DEV-03, frozen)",
    ),
}


def local(t: float) -> str:
    return datetime.fromtimestamp(t, TZ).strftime("%H:%M:%S.%f")[:-3]


def frames():
    with gzip.open(HERE / "candump-full.log.gz", "rt") as f:
        for line in f:
            m = FRAME.match(line)
            if m:
                yield float(m[1]), m[2], m[4]


def sigint_time() -> float:
    with gzip.open(HERE / "simulator-debug.log.gz", "rt") as f:
        for line in f:
            if "received SIGINT" in line:
                stamp = datetime.fromisoformat(line.split(" - ")[0]).replace(tzinfo=TZ)
                return stamp.timestamp()
    raise SystemExit("no SIGINT line in the simulator log")


def main() -> None:
    rows = list(frames())
    ids = Counter(cid for _, cid, _ in rows)
    error_frames = sum("ERRORFRAME" in data for _, _, data in rows)
    stop = sigint_time()

    exchanges = []  # (t_request, request_hex, reply_hex or None, latency or None)
    current = None
    for t, cid, data in rows:
        raw = bytes.fromhex(data.replace(" ", "")) if "ERRORFRAME" not in data else b""
        if cid == "7DF":
            if current:
                exchanges.append(current[:4])
            current = [t, raw[1 : 1 + (raw[0] & 0x0F)].hex(), None, None, None, 0]
        elif cid == "7E8" and current and current[2] is None:
            if current[3] is None:
                current[3] = t - current[0]
            kind = raw[0] >> 4
            if kind == 0:
                current[2] = raw[1 : 1 + (raw[0] & 0x0F)].hex()
            elif kind == 1:
                current[5] = ((raw[0] & 0x0F) << 8) | raw[1]
                current[4] = bytearray(raw[2:8])
            elif kind == 2 and current[4] is not None:
                current[4] += raw[1:8]
                if len(current[4]) >= current[5]:
                    current[2] = bytes(current[4][: current[5]]).hex()
    if current:
        exchanges.append(current[:4])

    answered = [e for e in exchanges if e[2] is not None]
    silent = [e for e in exchanges if e[2] is None]
    before_stop = [e for e in silent if e[0] < stop]
    after_stop = [e for e in silent if e[0] >= stop]
    latencies = sorted(e[3] for e in answered)
    duration = rows[-1][0] - rows[0][0]

    print(f"capture window      {local(rows[0][0])} to {local(rows[-1][0])} PDT, {duration:.1f} s")
    print(f"simulator SIGINT    {local(stop)}")
    print(f"frames              {len(rows)} total: " + ", ".join(f"{k} {v}" for k, v in sorted(ids.items())))
    print(f"error frames        {error_frames}")
    print(f"requests            {len(exchanges)}")
    print(f"answered            {len(answered)}")
    print(f"unanswered          {len(silent)}: {len(before_stop)} while running, {len(after_stop)} after SIGINT")
    print(
        "reply latency ms    "
        f"median {statistics.median(latencies) * 1e3:.2f}, "
        f"p99 {latencies[int(len(latencies) * 0.99)] * 1e3:.2f}, "
        f"max {latencies[-1] * 1e3:.2f}  (request to first reply frame, n={len(latencies)})"
    )

    print("\nrequest -> reply, count, verdict")
    pairs = Counter((e[1], e[2]) for e in answered)
    for (request, reply), count in sorted(pairs.items()):
        if request in EXPECTED:
            expected = EXPECTED[request]
            verdict = "acceptance MATCH" if expected == reply else f"acceptance DIFFERS, expected {expected}"
        elif request in OTHER_REFERENCE:
            ref, source = OTHER_REFERENCE[request]
            verdict = ("matches " if ref == reply else "DIFFERS from ") + source
        elif request == "03":
            verdict = "see DTC sequence"
        else:
            verdict = "no reference"
        print(f"  {request:6} -> {reply:44} x{count:<5} {verdict}")

    print("\nunanswered while the simulator was running")
    for request, count in sorted(Counter(e[1] for e in before_stop).items()):
        print(f"  {request:6} x{count}")
    print("unanswered after SIGINT")
    for request, count in sorted(Counter(e[1] for e in after_stop).items()):
        print(f"  {request:6} x{count}")

    print("\nDTC sequence (modes 03 and 04, in order)")
    for t, request, reply, _ in exchanges:
        if request in ("03", "04"):
            print(f"  {local(t)}  {request:4} -> {reply}")

    print(f"\ntester flow-control frames on 0x7E0: {ids.get('7E0', 0)}")
    multi = sorted({e[1] for e in answered if len(e[2]) > 14})
    print(f"multi-frame answers: {', '.join(multi)}")


if __name__ == "__main__":
    main()
