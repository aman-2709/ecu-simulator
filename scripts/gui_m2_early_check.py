#!/usr/bin/env python3
"""GUI M2 early check (decisions/0010 §9.2) -- run through scripts/run_gui_m2_early_check.sh.

On vcan, in a namespace: client configurations at N requests each, over R rounds with the
condition order rotated each round (§9.2's condition rotation, taken early). Wire latency
comes from candump -L, pairing each 0x7DF request with the first 0x7E8 frame before the
next request. Dispatch latency comes from the events' dispatch_us, harvested during fixed
pauses between request segments so the whole run's dispatch_us is captured without adding
load during a request (task-12). It prints one JSON line per condition per round, incremental
per-step cost lines, a P5 quiesce check, and "STOP" if an M4 latency criterion is already
missed, judged against that round's own condition 1. Early, not acceptance: P1-P9 are judged
at M4.
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

MEDIAN_LIMIT_MS = 0.10          # 0010 §9.2 P1
P99_LIMIT_MS = 0.50             # 0010 §9.2 P2
UPGRADE_DEADLINE_S = 5.0
CPU_ROOT = Path("/sys/devices/system/cpu")

# task-12 item 3: --conditions labels -> (api, readers, stalled_client). 1 and 2 are the
# original M2 early check; r1/r3 isolate the cost of reading clients alone; 4 is today's
# condition 4 (3 readers + 1 stalled), unchanged.
CONDITIONS: dict[str, tuple[bool, int, bool]] = {
    "1": (False, 0, False),
    "2": (True, 0, False),
    "r1": (True, 1, False),
    "r3": (True, 3, False),
    "4": (True, 3, True),
}
DEFAULT_CONDITIONS = ("1", "2", "4")          # today's early check, unchanged (task-12 item 3)
STEP_ORDER = ("1", "2", "r1", "r3", "4")      # the fixed cost order for incremental steps (item 5)

# task-12 item 4: requests go out in SEGMENTS equal-ish segments (500 each at n=5000), each
# followed by a fixed pause. In every API condition the pause harvests
# GET /api/v1/exchanges?after=&limit=HARVEST_LIMIT (<= observe/limits.py EXCHANGES_MAX_LIMIT
# and HISTORY_MAX_EVENTS, both 500): at n=5000 a segment is exactly one harvest's worth, so
# nothing is evicted from the 500-event history between harvests if nothing else consumes it.
SEGMENTS = 10
PAUSE_S = 0.05
HARVEST_LIMIT = 500


def requests(n: int) -> list[bytes]:
    out: list[bytes] = []
    while len(out) < n:
        out.extend(EVERY_100 if len(out) % 100 == 0 and out else [MIX[len(out) % 2]])
    return out[:n]


def segment_sizes(n: int, segments: int = SEGMENTS) -> list[int]:
    """n split into `segments` parts, as equal as possible (task-12 item 4: "10 equal
    segments, 500 each at 5,000"); any remainder is spread one-per-segment from the front,
    so every size differs from every other by at most 1."""
    base, extra = divmod(n, segments)
    return [base + 1 if i < extra else base for i in range(segments)]


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


def harvest_problem(body: dict, after: int) -> str | None:
    """A gap or a missing seq in one harvest response (task-12 item 4): a hole in
    dispatch_us cannot be filled after the fact, so this always stops the run. `body` is
    the parsed GET /api/v1/exchanges response (`watermark`, `oldest_seq`, `gap`, `events`)."""
    if body["gap"]:
        return f"harvest gap: after={after} oldest_seq={body['oldest_seq']}"
    want = after
    for event in body["events"]:
        want += 1
        if event["seq"] != want:
            return f"harvest missed a seq: after={after} expected {want}, got {event['seq']}"
    return None


async def harvest(after: int, dispatch: list[int], limit: int = HARVEST_LIMIT) -> tuple[int, str | None]:
    """One GET /api/v1/exchanges?after=&limit= during a pause (never while a request is in
    flight, task-12 item 4): extends `dispatch` with every harvested exchange's dispatch_us,
    in order, and returns the new watermark and any gap/miss problem. Never raises -- a bad
    harvest is folded into p5_problems like every other check, so the run still finishes and
    reports the rest of its numbers."""
    async with aiohttp.ClientSession() as s, s.get(f"{BASE}/api/v1/exchanges?after={after}&limit={limit}") as r:
        body = await r.json()
    problem = harvest_problem(body, after)
    dispatch.extend(e["dispatch_us"] for e in body["events"])
    new_after = body["events"][-1]["seq"] if body["events"] else after
    return new_after, problem


def rotate(order: tuple[str, ...], round_index: int) -> tuple[str, ...]:
    """Rotation r of the base order (0010 §9.2): round 0 unchanged, round 1 shifted left by
    one, and so on, wrapping after len(order) rounds."""
    k = round_index % len(order)
    return order[k:] + order[:k]


def parse_conditions(text: str) -> tuple[str, ...]:
    """--conditions as a comma list of labels (task-12 item 3), chosen from CONDITIONS.
    Condition 1 must be present -- every other condition is judged against its own round's
    condition 1 -- and each label at most once, so the rotation and the position/step
    summaries stay well defined."""
    labels = tuple(text.split(","))
    unknown = [label for label in labels if label not in CONDITIONS]
    if unknown:
        raise ValueError(f"unknown condition(s) {unknown}, choose from {sorted(CONDITIONS)}")
    if len(set(labels)) != len(labels):
        raise ValueError(f"--conditions must not repeat a condition: {text!r}")
    if "1" not in labels:
        raise ValueError("--conditions must include condition 1 (every other condition is judged against it)")
    return labels


def parse_order(text: str, allowed: tuple[str, ...]) -> tuple[str, ...]:
    """--order as an explicit, fixed order, e.g. "r1,4,2,1"; it must be a permutation of
    `allowed` (the labels this run's --conditions gave)."""
    order = tuple(text.split(","))
    if sorted(order) != sorted(allowed):
        raise ValueError(f"--order must be a permutation of {allowed}, got {text!r}")
    return order


def round_order(
    round_index: int, conditions: tuple[str, ...], order_override: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """The order for one round. --order is the fixed-order control: given, it is used
    unchanged for every round ("instead" of the rotation). Absent, round r rotates
    `conditions` by r (0010 §9.2)."""
    if order_override is not None:
        return order_override
    return rotate(conditions, round_index)


def judge(base: dict, r: dict, label: str) -> list[str]:
    """Stop reasons for one condition, judged against its own round's condition 1
    (0010 §9.2 P1-P3). Never compares condition 1's latency to itself."""
    reasons = []
    if label != "1":
        if r["median_ms"] > base["median_ms"] + MEDIAN_LIMIT_MS:
            reasons.append(f"median {r['median_ms']} > condition 1's {base['median_ms']} + {MEDIAN_LIMIT_MS}")
        if r["p99_ms"] > base["p99_ms"] + P99_LIMIT_MS:
            reasons.append(f"p99 {r['p99_ms']} > condition 1's {base['p99_ms']} + {P99_LIMIT_MS}")
    if r["lost"] > 0:
        reasons.append(f"lost {r['lost']}")
    if r.get("p5_problems"):
        reasons.append(f"p5_problems {r['p5_problems']}")
    return reasons


def reader_problems(readers: list[dict], issued_seq: int) -> list[str]:
    """Reasons a reader's run isn't trustworthy (task-12 item 2): it ended some way other
    than "stopped", or it received fewer exchanges than the condition published.

    "The condition published" is taken as `issued_seq` from the final /status, not the
    harvested seq count: it is already available at quiesce (no dependency on the harvest
    succeeding, which this same run also checks independently), and it is the quantity
    P5(a) already reconciles (`issued_seq = published + handoff_dropped`). In every reader
    condition here P6 requires `handoff_dropped = 0`, so `issued_seq` equals `published` in
    practice; using it is the more conservative (never smaller) of the two choices the
    brief allows.
    """
    problems = []
    for i, r in enumerate(readers):
        if r["ended"] != "stopped":
            problems.append(f"reader {i} ended: {r['ended']}")
        if issued_seq > 0 and r["exchanges"] < issued_seq:
            problems.append(f"reader {i} exchanges {r['exchanges']} < issued_seq {issued_seq}")
    return problems


def steps(conditions: tuple[str, ...]) -> list[tuple[str, str]]:
    """Consecutive pairs from the fixed cost order (1, 2, r1, r3, 4) that are both present
    in this run's --conditions (task-12 item 5: "as present")."""
    present = [c for c in STEP_ORDER if c in conditions]
    return list(zip(present, present[1:], strict=False))


def incremental(a: dict, b: dict) -> dict:
    """The incremental wire and dispatch cost of one step a->b, from two condition results
    of the same round (task-12 item 5). Dispatch is omitted when either side has none
    (condition 1 has no API and so no `dispatch_all_ms`)."""
    out = {
        "median_wire_ms": round(b["median_ms"] - a["median_ms"], 3),
        "p99_wire_ms": round(b["p99_ms"] - a["p99_ms"], 3),
    }
    da, db = a.get("dispatch_all_ms"), b.get("dispatch_all_ms")
    if da is not None and db is not None:
        out["median_dispatch_ms"] = round(db["median_ms"] - da["median_ms"], 3)
        out["p99_dispatch_ms"] = round(db["p99_ms"] - da["p99_ms"], 3)
    return out


def step_summary(records: list[dict]) -> dict[str, dict[str, float]]:
    """Mean and range of one step's incremental-cost fields across rounds (task-12 item 5)."""
    keys: set[str] = set()
    for r in records:
        keys.update(r)
    out = {}
    for key in sorted(keys):
        vals = [r[key] for r in records if key in r]
        if not vals:
            continue
        out[key] = {"mean": round(statistics.mean(vals), 3), "min": min(vals), "max": max(vals)}
    return out


def position_summary(records: list[dict]) -> dict[tuple[str, int], dict[str, float]]:
    """For each (condition, position) seen across rounds: the median of its per-round
    medians, its median and p99 excess over that round's own condition 1, how many rounds
    it stopped, and the sample count -- so load (condition) and order (position) can be
    read off directly (0010 §9.2)."""
    groups: dict[tuple[str, int], list[dict]] = {}
    for rec in records:
        groups.setdefault((rec["number"], rec["position"]), []).append(rec)
    out = {}
    for key, recs in groups.items():
        out[key] = {
            "median_ms": round(statistics.median(r["median_ms"] for r in recs), 3),
            "excess_ms": round(statistics.median(r["excess_ms"] for r in recs), 3),
            "p99_excess_ms": round(statistics.median(r["p99_excess_ms"] for r in recs), 3),
            "n": len(recs),
            "stops": sum(1 for r in recs if r["verdict"] == "STOP"),
        }
    return out


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
    When given, `dispatch` collects every exchange's dispatch_us (reader 0, as a cross-check
    against the full-run harvest)."""
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
    n: int, api: bool, readers: int, stalled_client: bool, workdir: Path, *,
    round_index: int = 0,
    pause_s: float = PAUSE_S,
    start_dump: Callable[[Path], tuple[subprocess.Popen[str], Any]] = _default_start_dump,
    start_sim: Callable[[bool], subprocess.Popen[str]] = start_simulator,
    make_tester: Callable[[], Any] = Tester,
) -> dict:
    """Run one client configuration on vcan (task-12 item 3): `readers` reading WebSocket
    clients (0, 1 or 3) and, if `stalled_client`, one raw socket that never reads (today's
    condition 4 is readers=3, stalled_client=True). Requests go out in SEGMENTS equal-ish
    segments (task-12 item 4); each is followed by a `pause_s` pause, during which -- never
    while a request is in flight -- every API condition harvests
    GET /api/v1/exchanges?after=&limit=HARVEST_LIMIT and keeps each exchange's dispatch_us.
    Stops the simulator, candump, the reader and stalled-client tasks and the tester's
    sockets on every exit, including an exception at any step (0010 §9.2), via `_teardown`.
    `start_dump`, `start_sim` and `make_tester` are injectable so the cleanup path can be
    unit-tested with fakes, never real processes. `round_index` keeps each round's raw
    capture as a separate file, so a later round never overwrites an earlier one's evidence.
    """
    log = workdir / f"candump-{round_index}-{int(api)}{readers}{int(stalled_client)}.log"
    dump, dump_log_fh = start_dump(log)
    sim: subprocess.Popen[str] | None = None
    tester: Any | None = None
    tasks: list[asyncio.Task[Any]] = []
    stop = asyncio.Event()
    result: dict = {}
    try:
        sim = start_sim(api)
        seen: list[list[int]] = [[] for _ in range(readers)]
        dispatch0: list[int] = []
        dispatch_all: list[int] = []
        reconnects: list[int] = []
        stalled_ids: list[int] = []
        harvest_problems: list[str] = []
        overruns_ms: list[float] = []
        if readers or stalled_client:
            tasks = [asyncio.create_task(reader(stop, seen[i], dispatch0 if i == 0 else None))
                     for i in range(readers)]
            if readers:
                for _ in range(40):                                     # readers first, so the stalled id is known
                    if (await status())["clients"] >= readers:
                        break
                    await asyncio.sleep(0.05)
                else:
                    raise RuntimeError(f"readers never reached {readers} in /status (the wait must fail loudly)")
            if stalled_client:
                tasks.append(asyncio.create_task(stalled(stop, reconnects, stalled_ids)))
            await asyncio.sleep(0.5)
        tester = make_tester()
        reqs = requests(n)
        offset = 0
        run_elapsed = 0.0
        last_seq = 0
        tester_lost = 0
        for size in segment_sizes(n):
            chunk = reqs[offset:offset + size]
            offset += size
            started = time.monotonic()
            tester_lost += await asyncio.to_thread(tester.run, chunk)
            run_elapsed += time.monotonic() - started
            pause_started = time.monotonic()
            if api:
                last_seq, problem = await harvest(last_seq, dispatch_all)
                if problem:
                    harvest_problems.append(problem)
            spent = time.monotonic() - pause_started
            remaining = pause_s - spent
            if remaining > 0:
                await asyncio.sleep(remaining)
            else:
                overruns_ms.append(round((spent - pause_s) * 1000, 3))    # the fetch alone overran the pause
        tester.close()
        tester = None
        await asyncio.sleep(1.0)                                          # let the publisher drain
        if api:
            last_seq, problem = await harvest(last_seq, dispatch_all)     # the tail after the last segment
            if problem:
                harvest_problems.append(problem)
        # "readers" (the int count) is deliberately not a top-level field here: the existing
        # "readers" field name is the per-reader detail list added below, and task-12's "do
        # not rename existing fields" rule means that name stays the list, not this count.
        result.update(api=api, clients=bool(readers or stalled_client), reader_count=readers,
                       stalled=stalled_client, requests=n, seconds=round(run_elapsed, 2),
                       rate_rps=round(n / run_elapsed, 1), tester_timeouts=tester_lost,
                       pause_s=pause_s, pause_overruns_ms=overruns_ms)
        if api:
            result["dispatch_all_ms"] = dispatch_summary(dispatch_all)    # 0010 §9.2: the whole run, not just 500
            if stalled_client:
                result["stalled_before_stop"] = ledgers_of(await status(), stalled_ids)
            stop.set()
            ended = await asyncio.gather(*tasks, return_exceptions=True)
            tasks = []
            for _ in range(40):                                          # quiesce: every close resolved
                state = await status()
                if state["clients"] == 0 and state["closed_unresolved"] == 0:
                    break
                await asyncio.sleep(0.25)
            result["p5_problems"] = p5(state) + harvest_problems
            if state["clients"] != 0:
                result["p5_problems"].append(f"quiesce timed out: clients = {state['clients']}")
            result["inconclusive"] = unresolved(state)
            result["delivery_unknown_allowed"] = allowed_unknown(state)  # reported, never silently passed
            result["forced_disconnects"] = state["forced_disconnects"]
            result["connections_opened"] = state["connections_opened"]
            result["reader_seq_ok"] = all(
                s == sorted(set(s)) and (state["issued_seq"] == 0 or s) for s in seen
            )                                                             # empty is not ok once exchanges publish
            if readers:
                result["readers"] = [{"exchanges": len(seen[i]), "ended": e if isinstance(e, str) else repr(e)}
                                     for i, e in enumerate(ended[:readers])]
                result["dispatch_reader0_ms"] = dispatch_summary(dispatch0)
                result["p5_problems"].extend(reader_problems(result["readers"], state["issued_seq"]))
            if stalled_client:
                result["reconnects"] = len(reconnects)
                result["stalled_ids"] = stalled_ids
                result["stalled_at_quiesce"] = ledgers_of(state, stalled_ids)
                if len(reconnects) > state["forced_disconnects"]:
                    result["p5_problems"].append(f"P5(e) harness reconnects {len(reconnects)} > forced_disconnects")
                stalled_result = ended[readers]
                if isinstance(stalled_result, BaseException):
                    result["p5_problems"].append(f"stalled-client task raised {stalled_result!r}")
    finally:
        await _teardown(tester, tasks, stop, sim, dump, dump_log_fh)
    samples, lost = latencies(log)
    if not samples:
        raise RuntimeError(f"no request/reply pairs in {log}: the capture or the tester failed")
    result.update(median_ms=round(statistics.median(samples), 3), p99_ms=round(samples[int(len(samples) * 0.99)], 3),
                  lost=lost, paired=len(samples))
    return result


def positive_int(text: str) -> int:
    """--rounds must be at least 1 (task-12 item 1): an argparse error (exit 2), never a
    silent fall-through to "within the limits" for a run that did nothing."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"--rounds must be at least 1, not {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=5000)
    parser.add_argument("--rounds", type=positive_int, default=1)
    parser.add_argument("--conditions", type=str, default=",".join(DEFAULT_CONDITIONS),
                         help="comma list from 1,2,r1,r3,4 (default: 1,2,4); must include 1")
    parser.add_argument("--order", type=str, default=None,
                         help="a fixed order used every round, e.g. r1,4,2,1 (default: rotate --conditions)")
    parser.add_argument("--captures", type=Path, default=None,
                         help="write candump logs here instead of a temp dir")
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    conditions = parse_conditions(args.conditions)
    order_override = parse_order(args.order, conditions) if args.order else None
    work = args.captures if args.captures is not None else Path(tempfile.mkdtemp(prefix="gui-m2-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"host: {json.dumps(host_info())}")
    print(f"pause_s: {PAUSE_S}")
    stop = inconclusive = False
    records: list[dict] = []
    step_records: dict[tuple[str, str], list[dict]] = {}
    for round_index in range(args.rounds):
        order = round_order(round_index, conditions, order_override)
        by_label: dict[str, dict] = {}
        for position, label in enumerate(order, start=1):
            api, readers, stalled_client = CONDITIONS[label]
            r = await condition(args.n, api, readers, stalled_client, work, round_index=round_index)
            r["round"], r["position"], r["number"] = round_index, position, label
            print(json.dumps(r))
            by_label[label] = r
        base = by_label["1"]
        for label, r in by_label.items():
            reasons = judge(base, r, label)
            verdict = "STOP" if reasons else "within limits"
            if reasons:
                print(f"STOP reasons, round {round_index} condition {label}: {reasons}")
            stop |= bool(reasons)
            inconclusive |= bool(r.get("inconclusive"))
            records.append({"number": label, "position": order.index(label) + 1,
                            "median_ms": r["median_ms"], "excess_ms": round(r["median_ms"] - base["median_ms"], 3),
                            "p99_ms": r["p99_ms"], "p99_excess_ms": round(r["p99_ms"] - base["p99_ms"], 3),
                            "verdict": verdict})
            excess = {"median_excess_ms": records[-1]["excess_ms"],
                      "p99_excess_ms": records[-1]["p99_excess_ms"], "verdict": verdict}
            print(f"excess round {round_index} condition {label}: {json.dumps(excess)}")
        for a, b in steps(conditions):
            if a in by_label and b in by_label:
                inc = incremental(by_label[a], by_label[b])
                print(f"incremental {a}->{b} round {round_index}: {json.dumps(inc)}")
                step_records.setdefault((a, b), []).append(inc)
    print(f"captures in {work}")
    for (number, position), stats in sorted(position_summary(records).items()):
        print(f"summary condition {number} position {position}: {json.dumps(stats)}")
    for (a, b), vals in step_records.items():
        print(f"incremental summary {a}->{b}: {json.dumps(step_summary(vals))}")
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
