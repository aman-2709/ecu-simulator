#!/usr/bin/env python3
"""GUI M2 early check (decisions/0010 §9.2) -- run through scripts/run_gui_m2_early_check.sh.

On vcan, in a namespace: conditions 1, 2 and 4 at N requests each, over R rounds with the
condition order rotated each round (§9.2's condition rotation, taken early). Wire latency
comes from candump -L, pairing each 0x7DF request with the first 0x7E8 frame before the
next request. Dispatch latency comes from the events' dispatch_us. It prints one JSON line
per condition per round, a P5 quiesce check, and "STOP" if an M4 latency criterion is
already missed, judged against that round's own condition 1. Early, not acceptance:
P1-P9 are judged at M4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import aiohttp
import isotp

IFACE = "vcan0"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
CAN_ISOTP_SF_BROADCAST = 0x0800
MIX = [b"\x01\x0d", b"\x01\x10"]
EVERY_100 = [b"\x01\x00", b"\x01\x20", b"\x01\x40", b"\x09\x02"]
FRAME = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-F]{3})#([0-9A-F]*)")

ROTATION_BASE = (1, 2, 4)
MEDIAN_LIMIT_MS = 0.10          # 0010 §9.2 P1
P99_LIMIT_MS = 0.50             # 0010 §9.2 P2
UPGRADE_DEADLINE_S = 5.0
CPU_ROOT = Path("/sys/devices/system/cpu")


def requests(n: int) -> list[bytes]:
    out: list[bytes] = []
    while len(out) < n:
        out.extend(EVERY_100 if len(out) % 100 == 0 and out else [MIX[len(out) % 2]])
    return out[:n]


class Tester:
    """The same shape as tests/integration/conftest.py FunctionalTester."""

    def __init__(self) -> None:
        self.tx = isotp.socket()
        self.tx.set_opts(optflag=isotp.socket.flags.TX_PADDING | CAN_ISOTP_SF_BROADCAST, txpad=0)
        self.tx.bind(IFACE, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=0, txid=0x7DF))
        self.rx = isotp.socket(timeout=1.0)
        self.rx.set_opts(optflag=isotp.socket.flags.TX_PADDING, txpad=0)
        self.rx.bind(IFACE, isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=0x7E8, txid=0x7E0))

    def run(self, reqs: list[bytes]) -> int:
        lost = 0
        for payload in reqs:
            self.tx.send(payload)
            try:
                self.rx.recv()
            except TimeoutError:
                lost += 1
        return lost

    def close(self) -> None:
        self.tx.close()
        self.rx.close()


def start_simulator(api: bool) -> subprocess.Popen[str]:
    """Start it and wait for its own "ready" line; drain stderr so the pipe never fills."""
    cmd = [sys.executable, "-m", "ecu_simulator", "--interface", IFACE, "--log-level", "INFO"]
    if api:
        cmd += ["--api", f"127.0.0.1:{PORT}"]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, cwd=tempfile.mkdtemp())
    ready = threading.Event()

    def pump() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if "ecu-simulator ready on" in line:
                ready.set()

    threading.Thread(target=pump, daemon=True).start()
    if not ready.wait(10):
        proc.kill()
        raise RuntimeError("simulator did not become ready within 10 s")
    return proc


def latencies(log: Path) -> tuple[list[float], int]:
    frames = [(float(m[1]), m[2]) for line in log.read_text().splitlines() if (m := FRAME.search(line))]
    samples, lost = [], 0
    starts = [i for i, (_, cid) in enumerate(frames) if cid == "7DF"]
    for k, i in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(frames)
        reply = next((t for t, cid in frames[i + 1:end] if cid == "7E8"), None)
        if reply is None or reply - frames[i][0] > 1.0:
            lost += 1
        else:
            samples.append((reply - frames[i][0]) * 1000)
    return sorted(samples), lost


def dispatch_summary(values_us: Sequence[int]) -> dict[str, float] | None:
    """Median and p99 dispatch latency in ms from dispatch_us (microseconds), or None for no
    samples (events.py:91). Condition 1 has no API and so no dispatch samples at all."""
    if not values_us:
        return None
    ms = sorted(v / 1000 for v in values_us)
    return {"median_ms": round(statistics.median(ms), 3), "p99_ms": round(ms[int(len(ms) * 0.99)], 3),
            "n": len(ms)}


def rotate(order: tuple[int, ...], round_index: int) -> tuple[int, ...]:
    """Rotation r of the base order (0010 §9.2): round 0 unchanged, round 1 shifted left by
    one, and so on, wrapping after len(order) rounds."""
    k = round_index % len(order)
    return order[k:] + order[:k]


def parse_order(text: str) -> tuple[int, ...]:
    """--order as an explicit, fixed order, e.g. "2,4,1"; it must be a permutation of the
    three M4 conditions this harness runs."""
    order = tuple(int(x) for x in text.split(","))
    if sorted(order) != sorted(ROTATION_BASE):
        raise ValueError(f"--order must be a permutation of {ROTATION_BASE}, got {text!r}")
    return order


def round_order(round_index: int, order_override: tuple[int, ...] | None) -> tuple[int, ...]:
    """The order for one round. --order is the fixed-order control: given, it is used
    unchanged for every round ("instead" of the rotation). Absent, round r rotates the
    base (1, 2, 4) by r (0010 §9.2)."""
    if order_override is not None:
        return order_override
    return rotate(ROTATION_BASE, round_index)


def judge(base: dict, r: dict, number: int) -> list[str]:
    """Stop reasons for one condition, judged against its own round's condition 1
    (0010 §9.2 P1-P3). Never compares condition 1's latency to itself."""
    reasons = []
    if number != 1:
        if r["median_ms"] > base["median_ms"] + MEDIAN_LIMIT_MS:
            reasons.append(f"median {r['median_ms']} > condition 1's {base['median_ms']} + {MEDIAN_LIMIT_MS}")
        if r["p99_ms"] > base["p99_ms"] + P99_LIMIT_MS:
            reasons.append(f"p99 {r['p99_ms']} > condition 1's {base['p99_ms']} + {P99_LIMIT_MS}")
    if r["lost"] > 0:
        reasons.append(f"lost {r['lost']}")
    if r.get("p5_problems"):
        reasons.append(f"p5_problems {r['p5_problems']}")
    return reasons


def position_summary(records: list[dict]) -> dict[tuple[int, int], dict[str, float]]:
    """For each (condition, position) seen across rounds: the median of its per-round
    medians and of its excess over that round's own condition 1, so load (condition) and
    order (position) can be read off directly (0010 §9.2)."""
    groups: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for rec in records:
        groups.setdefault((rec["number"], rec["position"]), []).append((rec["median_ms"], rec["excess_ms"]))
    return {key: {"median_ms": round(statistics.median(m for m, _ in vals), 3),
                  "excess_ms": round(statistics.median(e for _, e in vals), 3), "n": len(vals)}
            for key, vals in groups.items()}


def cpu_governors(root: Path = CPU_ROOT) -> list[str]:
    """The scaling_governor of every CPU, in order; "unknown" where it cannot be read
    (0010 §9.2: recorded once per run)."""
    cpus = sorted((d for d in root.glob("cpu[0-9]*") if d.is_dir()), key=lambda d: int(d.name[3:]))
    out = []
    for cpu in cpus:
        try:
            out.append((cpu / "cpufreq" / "scaling_governor").read_text().strip())
        except OSError:
            out.append("unknown")
    return out


def host_info() -> dict:
    """Once per run: the CPU governor of every CPU, nproc and the kernel release. nproc is
    the scheduler affinity count, matching the `nproc` command -- not os.cpu_count(), which
    ignores any affinity restriction."""
    return {"cpu_governors": cpu_governors(), "nproc": len(os.sched_getaffinity(0)), "kernel": platform.release()}


async def reader(stop: asyncio.Event, seen: list[int], dispatch: list[int] | None = None) -> str:
    """Read until told to stop; return how it ended, so a reader that died is never silent.
    When given, `dispatch` collects every exchange's dispatch_us (reader 0, for condition 4)."""
    async with aiohttp.ClientSession() as s, s.ws_connect(f"{BASE}/api/v1/events", origin=BASE) as ws:
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(ws.receive(), 0.5)
            except TimeoutError:
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                return f"ended early: {msg.type.name} {ws.close_code}"
            event = json.loads(msg.data)
            if event.get("type") == "exchange":
                seen.append(event["seq"])
                if dispatch is not None:
                    dispatch.append(event["dispatch_us"])
    return "stopped"


def stalled_socket() -> socket.socket:
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.connect(("127.0.0.1", PORT))
    raw.sendall(
        f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nOrigin: {BASE}\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        .encode()
    )
    return raw


def read_upgrade_response(raw: Any, deadline_s: float = UPGRADE_DEADLINE_S) -> bytes:
    """Read until the header ends or the deadline passes; never blocks forever on a
    refusal. `raw` needs only settimeout() and recv(), so a fake stands in for tests."""
    raw.settimeout(deadline_s)
    data = b""
    try:
        while b"\r\n\r\n" not in data and len(data) < 4096:
            chunk = raw.recv(4096)
            if not chunk:
                break
            data += chunk
    except OSError:
        pass
    return data


def upgrade_ok(data: bytes) -> str | None:
    """None if the response is HTTP/1.1 101; otherwise the failure, carrying the status
    line (a refused upgrade must fail the run visibly, never pass silently)."""
    if not data:
        return "no response before the deadline"
    line = data.split(b"\r\n", 1)[0].decode(errors="replace")
    if line.startswith("HTTP/1.1 101"):
        return None
    return f"stalled-socket upgrade refused: {line!r}"


async def status() -> dict:
    async with aiohttp.ClientSession() as s, s.get(f"{BASE}/api/v1/status") as r:
        return (await r.json())["api"]


async def last500_dispatch() -> dict[str, float] | None:
    """Dispatch latency from the one final GET /exchanges?limit=500, taken after the tester
    finishes (the history keeps 500 events). Conditions 2 and 4 only; never polled or
    fetched mid-run, so as not to change condition 2 (0010 §9.2)."""
    async with aiohttp.ClientSession() as s, s.get(f"{BASE}/api/v1/exchanges?limit=500") as r:
        body = await r.json()
    return dispatch_summary([e["dispatch_us"] for e in body["events"]])


async def open_stalled(ids: list[int]) -> socket.socket:
    """Connect the stalled socket, require the 101 upgrade, and learn its connection id
    from /status (the id no one else had). Raises on a refused upgrade or a missing id, so
    a broken stalled client never passes silently (0010 §9.2). `raw` is closed on every
    failure path -- the upgrade check, the id poll, or a /status call that itself raises --
    so a failed connect or reconnect never leaks a socket."""
    before = {c["id"] for c in (await status())["connections"]}
    raw = stalled_socket()
    try:
        data = await asyncio.to_thread(read_upgrade_response, raw)
        problem = upgrade_ok(data)
        if problem is not None:
            raise RuntimeError(problem)
        for _ in range(40):
            new = {c["id"] for c in (await status())["connections"]} - before
            if new:
                ids.extend(sorted(new))
                return raw
            await asyncio.sleep(0.05)
        raise RuntimeError("stalled connection id never appeared in /status")
    except BaseException:
        raw.close()
        raise


async def stalled(stop: asyncio.Event, reconnects: list[int], ids: list[int]) -> None:
    raw, forced = await open_stalled(ids), 0
    try:
        while not stop.is_set():
            await asyncio.sleep(0.5)
            now = (await status())["forced_disconnects"]
            if now > forced:                                          # 0010 §9.2 condition 4: reconnect at once
                forced = now
                raw.close()
                raw = await open_stalled(ids)
                reconnects.append(now)
    finally:
        raw.close()


LEDGER_KEYS = ("id", "close_code", "client_dropped", "queued", "enqueued", "sent", "delivery_unknown")


def ledgers_of(api: dict, ids: list[int]) -> list[dict]:
    """The stalled connections' ledgers, trimmed to what shows whether they overflowed."""
    return [{k: led[k] for k in LEDGER_KEYS} for led in api["connections"] + api["closed_connections"]
            if led["id"] in ids]


def p5(api: dict) -> list[str]:
    problems = []
    if api["issued_seq"] != api["published"] + api["handoff_dropped"]:
        problems.append("P5(a) issued != published + handoff_dropped")
    ledgers = api["connections"] + api["closed_connections"]
    for led in ledgers:
        if led["offered"] != led["published_at_close"] - led["published_at_open"] or \
           led["offered"] != led["enqueued"] + led["client_dropped"] or \
           led["enqueued"] != led["sent"] + led["delivery_unknown"] + led["queued"] + led["discarded_on_close"]:
            problems.append(f"P5(b) ledger {led['id']}")
    t = api["closed_totals"]
    if not (t["offered"] == t["published_span"] == t["enqueued"] + t["client_dropped"]
            and t["enqueued"] == t["sent"] + t["delivery_unknown"] + t["discarded_on_close"]):
        problems.append("P5(b) closed_totals")
    if api["connections_opened"] != api["clients"] + t["connections"] + api["closed_unresolved"]:
        problems.append("connections_opened does not reconcile")
    if t["close_codes"].get("1013", 0) != api["forced_disconnects"]:
        problems.append("P5(e) 1013 closes != forced_disconnects")
    if t["close_codes"].get("1011", 0) != api["fanout_failed"] + api["writer_failed"]:
        problems.append("P5(e) 1011 closes != fanout_failed + writer_failed")
    return problems


ALLOWED = ("1013", "1006")   # 0010 P5(h): at most one delivery_unknown per forced or reset connection


def allowed_unknown(api: dict) -> dict[str, int]:
    """The delivery_unknown counts P5(h) allows, reported explicitly with the results."""
    by_code = api["closed_totals"]["delivery_unknown_by_close_code"]
    return {code: by_code.get(code, 0) for code in ALLOWED if by_code.get(code, 0)}


def unresolved(api: dict) -> list[str]:
    """Beyond the P5(h) allowance: the condition is inconclusive, never passed (0010 §9.2 P5(h))."""
    notes = []
    t = api["closed_totals"]
    if t["delivery_unknown_over_allowance"]:
        notes.append(f"{t['delivery_unknown_over_allowance']} closed connection(s) over the P5(h) allowance")
    for code, unknown in t["delivery_unknown_by_close_code"].items():
        if code in ALLOWED and unknown > t["close_codes"].get(code, 0):
            notes.append(f"delivery_unknown {unknown} > {t['close_codes'].get(code, 0)} connections closed {code}")
        elif code not in ALLOWED and unknown:
            notes.append(f"delivery_unknown {unknown} on connections closed {code} (allowance is 0)")
    for led in api["connections"] + api["closed_connections"]:
        limit = 1 if str(led["close_code"]) in ALLOWED else 0
        if led["delivery_unknown"] > limit:
            notes.append(f"connection {led['id']}: delivery_unknown {led['delivery_unknown']} > {limit}")
    if api["closed_unresolved"]:
        notes.append(f"closed_unresolved = {api['closed_unresolved']} at quiesce")
    return notes


def _default_start_dump(log: Path) -> tuple[subprocess.Popen[str], Any]:
    """The log file handle is returned alongside the process so it can be closed once
    candump no longer needs it (M6): Popen dup()s the fd but never takes ownership of it."""
    fh = log.open("w")
    return subprocess.Popen(["candump", "-L", IFACE], stdout=fh), fh


def _stop_process(proc: subprocess.Popen[str], timeout: float) -> None:
    """SIGINT, then wait; kill and wait again (no timeout) if it doesn't exit in time."""
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


async def _teardown(
    tester: Any | None, tasks: list[asyncio.Task[Any]], stop: asyncio.Event,
    sim: subprocess.Popen[str] | None, dump: subprocess.Popen[str], dump_log_fh: Any,
) -> None:
    """Stop everything condition() may have started, each step running whatever the one
    before it did or raised (0010 §9.2): the tester's sockets, the reader/stalled tasks,
    the simulator, candump, and finally candump's log file handle (M6). Nested try/finally,
    not a flat sequence, so one resource's failure to stop never skips the next one."""
    try:
        if tester is not None:
            tester.close()
    finally:
        try:
            if tasks:
                stop.set()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                if sim is not None:
                    _stop_process(sim, 10)
            finally:
                try:
                    _stop_process(dump, 5)
                finally:
                    if dump_log_fh is not None:
                        dump_log_fh.close()


async def condition(
    n: int, api: bool, clients: bool, workdir: Path, *,
    round_index: int = 0,
    start_dump: Callable[[Path], tuple[subprocess.Popen[str], Any]] = _default_start_dump,
    start_sim: Callable[[bool], subprocess.Popen[str]] = start_simulator,
    make_tester: Callable[[], Any] = Tester,
) -> dict:
    """Run one M4 condition on vcan. Stops the simulator, candump, the reader and
    stalled-client tasks and the tester's sockets on every exit, including an exception at
    any step (0010 §9.2), via `_teardown`. `start_dump`, `start_sim` and `make_tester` are
    injectable so the cleanup path can be unit-tested with fakes, never real processes.
    `round_index` keeps each round's raw capture as a separate file, so a later round
    never overwrites an earlier one's evidence."""
    log = workdir / f"candump-{round_index}-{int(api)}{int(clients)}.log"
    dump, dump_log_fh = start_dump(log)
    sim: subprocess.Popen[str] | None = None
    tester: Any | None = None
    tasks: list[asyncio.Task[Any]] = []
    stop = asyncio.Event()
    result: dict = {}
    try:
        sim = start_sim(api)
        seen: list[list[int]] = [[], [], []]
        dispatch0: list[int] = []
        reconnects: list[int] = []
        stalled_ids: list[int] = []
        if clients:
            tasks = [asyncio.create_task(reader(stop, seen[i], dispatch0 if i == 0 else None)) for i in range(3)]
            for _ in range(40):                                          # readers first, so the stalled id is known
                if (await status())["clients"] >= 3:
                    break
                await asyncio.sleep(0.05)
            tasks.append(asyncio.create_task(stalled(stop, reconnects, stalled_ids)))
            await asyncio.sleep(0.5)
        tester = make_tester()
        started = time.monotonic()
        tester_lost = await asyncio.to_thread(tester.run, requests(n))
        elapsed = time.monotonic() - started
        tester.close()
        tester = None
        await asyncio.sleep(1.0)                                         # let the publisher drain
        result.update(api=api, clients=clients, requests=n, seconds=round(elapsed, 2),
                       rate_rps=round(n / elapsed, 1), tester_timeouts=tester_lost)
        if api:
            result["dispatch_last500_ms"] = await last500_dispatch()     # 0010 §9.2: last 500, after the tester
            if clients:
                result["stalled_before_stop"] = ledgers_of(await status(), stalled_ids)
            stop.set()
            ended = await asyncio.gather(*tasks, return_exceptions=True)
            tasks = []
            for _ in range(40):                                          # quiesce: every close resolved
                state = await status()
                if state["clients"] == 0 and state["closed_unresolved"] == 0:
                    break
                await asyncio.sleep(0.25)
            result["p5_problems"] = p5(state)
            if state["clients"] != 0:
                result["p5_problems"].append(f"quiesce timed out: clients = {state['clients']}")
            if len(reconnects) > state["forced_disconnects"]:
                result["p5_problems"].append(f"P5(e) harness reconnects {len(reconnects)} > forced_disconnects")
            result["inconclusive"] = unresolved(state)
            result["delivery_unknown_allowed"] = allowed_unknown(state)  # reported, never silently passed
            result["forced_disconnects"] = state["forced_disconnects"]
            result["connections_opened"] = state["connections_opened"]
            result["reader_seq_ok"] = all(s == sorted(set(s)) for s in seen)
            if clients:
                result["reconnects"] = len(reconnects)
                result["readers"] = [{"exchanges": len(seen[i]), "ended": e if isinstance(e, str) else repr(e)}
                                     for i, e in enumerate(ended[:3])]
                result["stalled_ids"] = stalled_ids
                result["stalled_at_quiesce"] = ledgers_of(state, stalled_ids)
                result["dispatch_reader0_ms"] = dispatch_summary(dispatch0)
                if isinstance(ended[3], BaseException):
                    result["p5_problems"].append(f"stalled-client task raised {ended[3]!r}")
    finally:
        await _teardown(tester, tasks, stop, sim, dump, dump_log_fh)
    samples, lost = latencies(log)
    if not samples:
        raise RuntimeError(f"no request/reply pairs in {log}: the capture or the tester failed")
    result.update(median_ms=round(statistics.median(samples), 3), p99_ms=round(samples[int(len(samples) * 0.99)], 3),
                  lost=lost, paired=len(samples))
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=5000)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--order", type=str, default=None,
                         help="a fixed order used every round, e.g. 2,4,1 (default: rotate 1,2,4 each round)")
    args = parser.parse_args()
    order_override = parse_order(args.order) if args.order else None
    work = Path(tempfile.mkdtemp(prefix="gui-m2-"))
    print(f"host: {json.dumps(host_info())}")
    stop = inconclusive = False
    records: list[dict] = []
    for round_index in range(args.rounds):
        order = round_order(round_index, order_override)
        by_number: dict[int, dict] = {}
        for position, number in enumerate(order, start=1):
            r = await condition(args.n, number != 1, number == 4, work, round_index=round_index)
            r["round"], r["position"], r["number"] = round_index, position, number
            print(json.dumps(r))
            by_number[number] = r
        base = by_number[1]
        for number, r in by_number.items():
            reasons = judge(base, r, number)
            if reasons:
                print(f"STOP reasons, round {round_index} condition {number}: {reasons}")
            stop |= bool(reasons)
            inconclusive |= bool(r.get("inconclusive"))
            records.append({"number": number, "position": order.index(number) + 1,
                            "median_ms": r["median_ms"], "excess_ms": round(r["median_ms"] - base["median_ms"], 3)})
    print(f"captures in {work}")
    for (number, position), stats in sorted(position_summary(records).items()):
        print(f"summary condition {number} position {position}: {json.dumps(stats)}")
    if stop:
        print("STOP: report before M3")
        return 1
    if inconclusive:
        print("INCONCLUSIVE: delivery the ledger cannot vouch for; report the counts, not a pass")
        return 2
    print("within the M2 early-check limits")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
