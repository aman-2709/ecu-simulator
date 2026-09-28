#!/usr/bin/env python3
"""GUI M2 early check (decisions/0010 §9.2) -- run through scripts/run_gui_m2_early_check.sh.

On vcan, in a namespace: conditions 1, 2 and 4 at N requests each, one round. Wire latency
comes from candump -L, pairing each 0x7DF request with the first 0x7E8 frame before the
next request. It prints per-condition figures, P5 at quiesce for 2 and 4, and "STOP" if an
M4 latency criterion is already missed. Early, not acceptance: P1-P9 are judged at M4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import aiohttp
import isotp

IFACE = "vcan0"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
CAN_ISOTP_SF_BROADCAST = 0x0800
MIX = [b"\x01\x0d", b"\x01\x10"]
EVERY_100 = [b"\x01\x00", b"\x01\x20", b"\x01\x40", b"\x09\x02"]
FRAME = re.compile(r"\((\d+\.\d+)\)\s+\S+\s+([0-9A-F]{3})#([0-9A-F]*)")


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


async def reader(stop: asyncio.Event, seen: list[int]) -> str:
    """Read until told to stop; return how it ended, so a reader that died is never silent."""
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


async def status() -> dict:
    async with aiohttp.ClientSession() as s, s.get(f"{BASE}/api/v1/status") as r:
        return (await r.json())["api"]


async def open_stalled(ids: list[int]) -> socket.socket:
    """Connect the stalled socket and learn its connection id from /status (the id no one else had)."""
    before = {c["id"] for c in (await status())["connections"]}
    raw = stalled_socket()
    for _ in range(40):
        new = {c["id"] for c in (await status())["connections"]} - before
        if new:
            ids.extend(sorted(new))
            break
        await asyncio.sleep(0.05)
    return raw


async def stalled(stop: asyncio.Event, reconnects: list[int], ids: list[int]) -> None:
    raw, forced = await open_stalled(ids), 0
    while not stop.is_set():
        await asyncio.sleep(0.5)
        now = (await status())["forced_disconnects"]
        if now > forced:                                          # 0010 §9.2 condition 4: reconnect at once
            forced = now
            raw.close()
            raw = await open_stalled(ids)
            reconnects.append(now)
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


async def condition(n: int, api: bool, clients: bool, workdir: Path) -> dict:
    log = workdir / f"candump-{int(api)}{int(clients)}.log"
    dump = subprocess.Popen(["candump", "-L", IFACE], stdout=log.open("w"))
    sim = start_simulator(api)
    stop, tasks, seen, reconnects, stalled_ids = asyncio.Event(), [], [[], [], []], [], []
    if clients:
        tasks = [asyncio.create_task(reader(stop, seen[i])) for i in range(3)]
        for _ in range(40):                                          # readers first, so the stalled id is known
            if (await status())["clients"] >= 3:
                break
            await asyncio.sleep(0.05)
        tasks.append(asyncio.create_task(stalled(stop, reconnects, stalled_ids)))
        await asyncio.sleep(0.5)
    tester = Tester()
    started = time.monotonic()
    tester_lost = await asyncio.to_thread(tester.run, requests(n))
    elapsed = time.monotonic() - started
    tester.close()
    await asyncio.sleep(1.0)                                         # let the publisher drain
    result: dict = {"api": api, "clients": clients, "requests": n, "seconds": round(elapsed, 2),
                    "tester_timeouts": tester_lost}
    if api:
        if clients:
            result["stalled_before_stop"] = ledgers_of(await status(), stalled_ids)
        stop.set()
        ended = await asyncio.gather(*tasks, return_exceptions=True)
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
        result["delivery_unknown_allowed"] = allowed_unknown(state)   # reported, never silently passed
        result["forced_disconnects"] = state["forced_disconnects"]
        result["connections_opened"] = state["connections_opened"]
        result["reader_seq_ok"] = all(s == sorted(set(s)) for s in seen)
        if clients:
            result["reconnects"] = len(reconnects)
            result["readers"] = [{"exchanges": len(seen[i]), "ended": e if isinstance(e, str) else repr(e)}
                                 for i, e in enumerate(ended[:3])]
            result["stalled_ids"] = stalled_ids
            result["stalled_at_quiesce"] = ledgers_of(state, stalled_ids)
            if isinstance(ended[3], BaseException):
                result["p5_problems"].append(f"stalled-client task raised {ended[3]!r}")
    sim.send_signal(signal.SIGINT)
    sim.wait(10)
    dump.send_signal(signal.SIGINT)
    dump.wait(5)
    samples, lost = latencies(log)
    if not samples:
        raise RuntimeError(f"no request/reply pairs in {log}: the capture or the tester failed")
    result.update(median_ms=round(statistics.median(samples), 3), p99_ms=round(samples[int(len(samples) * 0.99)], 3),
                  lost=lost, paired=len(samples))
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=5000)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="gui-m2-"))
    results = [await condition(args.n, False, False, work), await condition(args.n, True, False, work),
               await condition(args.n, True, True, work)]
    base = results[0]
    stop = inconclusive = False
    for number, r in zip((1, 2, 4), results, strict=True):
        print(f"condition {number}: {json.dumps(r)}")
        if number != 1:
            stop |= r["median_ms"] > base["median_ms"] + 0.10 or r["p99_ms"] > base["p99_ms"] + 0.50
        stop |= r["lost"] > 0 or bool(r.get("p5_problems"))
        inconclusive |= bool(r.get("inconclusive"))
    print(f"captures in {work}")
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
