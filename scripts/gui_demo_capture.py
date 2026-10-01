#!/usr/bin/env python3
"""Screenshots of the observer page from a live session -- run through scripts/run_gui_demo.sh.

It expects to be inside a private network namespace that already has lo and a vcan0 up.
It starts the real simulator with --api 127.0.0.1:8765, the read-only traffic generator
(scripts/gui_demo_traffic.py) and headless Chrome, and drives Chrome over the DevTools
protocol: it loads the page, clicks the page's own view controls (a filter, pause), and
takes screenshots. It changes nothing in the page's code. The only interception is for the
loading shot: the page's first API requests are held for a moment in DevTools, then
released unchanged.

The simulator is stopped and restarted to show the disconnected and reconnected states,
and started once more with the no-scenario profile. Every process this starts is stopped
by its exact PID on every path.

--m3b runs the M3b browser checks (docs/plans/gui-m3b-graphs-design.md §12.2) against the
real simulator and the fault-injection test server (scripts/gui_fault_server.py, §12.3),
with a WebSocket wrapper added before the page's scripts, and the cost measurement of
§11.3; --m3b-long is the bounded-history case on its own. Each case's pass rule, observed
values and verdict go to m3b-results.json (m3b-long-results.json); any failure exits 1.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import datetime
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "src" / "ecu_simulator" / "profiles"
SCENARIO_PROFILE = PROFILES / "ice_scenario.yaml"
DEFAULT_PROFILE = PROFILES / "ice_default.yaml"
MOVING_PROFILE = ROOT / "docs" / "examples" / "ice_drive_cycle_stepped.yaml"
IFACE = "vcan0"
API = "127.0.0.1:8765"
PAGE = f"http://{API}/"
DEVTOOLS_PORT = 9222
WIDE = (1440, 900)
OWNER_WIDE = (2000, 1100)
NARROW = (390, 844)
MID = (1200, 900)          # M3b: the overflow check and the status-bar height only (not the log-rows check)
TRAFFIC_RATE = "4"


class Run:
    """The processes of one demo run, each stopped by its exact PID."""

    def __init__(self, outdir: Path) -> None:
        self.outdir = outdir
        self.procs: list[subprocess.Popen[bytes]] = []
        self.t0 = time.monotonic()
        self.logfile = (outdir / "capture.log").open("a")
        self.overflow_failures: list[str] = []
        self.case_failures: list[str] = []

    def log(self, message: str) -> None:
        line = f"[{time.monotonic() - self.t0:7.2f} s] {message}"
        print(line, flush=True)
        self.logfile.write(line + "\n")
        self.logfile.flush()

    def spawn(self, cmd: list[str], logname: str, env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
        out = (self.outdir / logname).open("ab")
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=ROOT, env=env)
        out.close()
        self.procs.append(proc)
        return proc

    def stop(self, proc: subprocess.Popen[bytes] | None, timeout: float = 5.0) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def stop_all(self) -> None:
        for proc in reversed(self.procs):
            self.stop(proc)
        self.logfile.close()


async def wait_ready(run: Run, proc: subprocess.Popen[bytes], logname: str, deadline_s: float = 15.0) -> None:
    """Wait for the simulator's own "ready" line in its log, then for the API to answer."""
    log = run.outdir / logname
    start = log.stat().st_size if log.exists() else 0
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the simulator exited early with {proc.returncode}; see {log}")
        with log.open("rb") as fh:
            fh.seek(start)
            if b"ecu-simulator ready on" in fh.read():
                break
        await asyncio.sleep(0.1)
    else:
        raise RuntimeError("the simulator did not become ready")
    async with aiohttp.ClientSession() as http:
        while time.monotonic() < deadline:
            try:
                async with http.get(f"http://{API}/api/v1/status") as resp:
                    if resp.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("the observer API did not answer")


def start_simulator(run: Run, profile: Path, logname: str) -> subprocess.Popen[bytes]:
    run.log(f"start simulator: --profile {profile.relative_to(ROOT)} --interface {IFACE} --api {API}")
    return run.spawn([sys.executable, "-m", "ecu_simulator", "--profile", str(profile), "--interface", IFACE,
                      "--api", API, "--log-level", "INFO"], logname)


def start_traffic(run: Run) -> subprocess.Popen[bytes]:
    run.log(f"start traffic: scripts/gui_demo_traffic.py --interface {IFACE} --rate {TRAFFIC_RATE}")
    return run.spawn([sys.executable, str(ROOT / "scripts" / "gui_demo_traffic.py"), "--interface", IFACE,
                      "--rate", TRAFFIC_RATE], "traffic.log")


class DevTools:
    """A minimal Chrome DevTools protocol client for one page target."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for msg in self.ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if "id" in data and data["id"] in self.pending:
                future = self.pending.pop(data["id"])
                if "error" in data:
                    future.set_exception(RuntimeError(f"DevTools: {data['error']}"))
                else:
                    future.set_result(data.get("result", {}))
            elif data.get("method") in self.handlers:
                self.handlers[data["method"]](data.get("params", {}))

    async def send(self, method: str, **params: Any) -> dict[str, Any]:
        msg_id = next(self.ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = future
        await self.ws.send_str(json.dumps({"id": msg_id, "method": method, "params": params}))
        return await asyncio.wait_for(future, 30)

    async def js(self, expression: str) -> Any:
        result = await self.send("Runtime.evaluate", expression=expression, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(f"page script failed: {result['exceptionDetails']}")
        return result["result"].get("value")

    async def wait_for(self, expression: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.js(expression):
                return
            await asyncio.sleep(0.2)
        raise RuntimeError(f"timed out waiting for: {expression}")

    async def viewport(self, width: int, height: int) -> None:
        await self.send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                        deviceScaleFactor=1, mobile=width < 600)

    async def centre(self, selector: str) -> tuple[float, float]:
        rect = await self.js(f"(function(){{var r=document.querySelector({json.dumps(selector)})"
                             ".getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2];})()")
        return float(rect[0]), float(rect[1])

    async def wheel(self, selector: str, delta_y: float) -> None:
        """A real mouse-wheel event over the element, the way a person scrolls."""
        x, y = await self.centre(selector)
        await self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        await self.send("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=delta_y)

    async def click(self, selector: str) -> None:
        """A real left click at the element's centre."""
        x, y = await self.centre(selector)
        await self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for kind in ("mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    async def shot(self, run: Run, name: str, full_page: bool = False) -> None:
        params: dict[str, Any] = {"format": "png"}
        if full_page:
            metrics = await self.send("Page.getLayoutMetrics")
            size = metrics["cssContentSize"]
            params.update(captureBeyondViewport=True,
                          clip={"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1})
        result = await self.send("Page.captureScreenshot", **params)
        (run.outdir / name).write_bytes(base64.b64decode(result["data"]))
        state = await self.js("document.getElementById('conn-text').textContent + ' | ' + "
                              "(document.getElementById('log-count').textContent || '')")
        run.log(f"screenshot {name}: {state}")


CONN = "document.getElementById('conn-text').textContent"
# Horizontal clipping: the vehicle panel's body, the page and the body, as [name, scrollWidth,
# clientWidth]. Any scrollWidth over its clientWidth is content cut off or scrolled sideways.
# M3b adds the graphs section and each uPlot box (§12.2 "Overflow").
OVERFLOW = """(function(){
  var v = document.getElementById('vehicle'), g = document.getElementById('graphs');
  var els = [['#vehicle', v], ['html', document.documentElement], ['body', document.body]];
  if (g) els.push(['#graphs', g]);
  Array.prototype.forEach.call(document.querySelectorAll('#graphs .uplot'), function (u, i) {
    els.push(['.uplot ' + i, u]);
  });
  return els.map(function (p) {
    return [p[0], p[1].scrollWidth, p[1].clientWidth];
  });
})()"""


async def check_overflow(run: Run, cdp: DevTools, label: str) -> None:
    """Record, in overflow-check.txt, whether anything overflows horizontally at this width.
    A failure does not stop the run (the screenshots are still wanted); main() exits 1."""
    measured = await cdp.js(OVERFLOW)
    bad = [f"{name} {scroll} > {client}" for name, scroll, client in measured if scroll > client]
    line = f"{label}: " + ", ".join(f"{n} {s}/{c}" for n, s, c in measured) + (f"  OVERFLOW: {bad}" if bad else "  ok")
    with (run.outdir / "overflow-check.txt").open("a") as fh:
        fh.write(line + "\n")
    run.log(f"horizontal overflow check, {line}")
    if bad:
        run.overflow_failures.append(line)
# One snapshot of the jump control against the DOM, taken in a single evaluation so both
# numbers describe the same moment. `counted` is every shown log row (exchange or marker)
# whose top edge is at or below the log box's bottom edge, counted here independently of
# the page's own code; `partial` is the row cut by that edge, if any. `overlap` is true if
# the control's box intersects the log box.
JUMP_STATE = """(function(){
  var b = document.getElementById('btn-follow'), w = document.getElementById('logwrap');
  var wr = w.getBoundingClientRect(), br = b.getBoundingClientRect();
  var rows = document.querySelectorAll('#log-body > tr'), counted = 0, partial = 0;
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i].getBoundingClientRect();
    if (r.height === 0) continue;
    if (r.top >= wr.bottom - 1) counted++; else if (r.bottom > wr.bottom + 1) partial++;
  }
  var shown = !b.classList.contains('is-off') && getComputedStyle(b).visibility !== 'hidden';
  var overlap = shown && br.left < wr.right && br.right > wr.left && br.top < wr.bottom && br.bottom > wr.top;
  var m = /^([0-9,]+) rows? below/.exec(b.textContent);
  return {shown: shown, text: b.textContent, n: m ? Number(m[1].replace(/,/g, '')) : null,
          counted: counted, partial: partial, overlap: overlap,
          in_header: !!b.closest('.panel__head'), last_seq: document.getElementById('log-count').textContent};
})()"""


async def jump_pair(run: Run, cdp: DevTools, label: str) -> None:
    """At the current width: a real wheel scroll up over the log, a shot of the header's
    "N rows below" control with N checked against the DOM, then a real click on it and a
    shot back at the newest row with the control hidden."""
    await cdp.js("document.getElementById('log-panel').scrollIntoView({block: 'start'})")
    await asyncio.sleep(0.5)
    await cdp.wheel("#logwrap", -700)
    await asyncio.sleep(2.5)
    before = await cdp.js(JUMP_STATE)
    await cdp.shot(run, f"j-{label}-rows-below.png")
    after = await cdp.js(JUMP_STATE)
    run.log(f"j-{label} rows below, just before the shot: {before}")
    run.log(f"j-{label} rows below, just after the shot:  {after}")
    await cdp.click("#btn-follow")
    await asyncio.sleep(1.5)
    jumped = await cdp.js(JUMP_STATE)
    await cdp.shot(run, f"j-{label}-jumped.png")
    run.log(f"j-{label} after the click: {jumped}")


async def launch_chrome(run: Run, chrome: str, profile_dir: str,
                        extra: tuple[str, ...] = ()) -> subprocess.Popen[bytes]:
    run.log(f"start headless Chrome: {chrome} {' '.join(extra)}".rstrip())
    proc = run.spawn([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                      "--no-first-run", "--no-default-browser-check", f"--user-data-dir={profile_dir}",
                      f"--remote-debugging-port={DEVTOOLS_PORT}", *extra, "about:blank"], "chrome.log",
                     # Chrome puts its singleton socket directory (com.google.Chrome.*) under
                     # TMPDIR and does not always remove it; keep it inside the profile
                     # directory, which is removed on exit.
                     env={**os.environ, "TMPDIR": profile_dir})
    return proc


async def page_target(http: aiohttp.ClientSession) -> str:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            async with http.get(f"http://127.0.0.1:{DEVTOOLS_PORT}/json/list") as resp:
                for target in await resp.json():
                    if target.get("type") == "page":
                        return str(target["webSocketDebuggerUrl"])
        except aiohttp.ClientError:
            pass
        await asyncio.sleep(0.2)
    raise RuntimeError("Chrome's DevTools endpoint did not come up")


async def demo(run: Run, chrome: str, mode: str = "m3a") -> None:
    profile_dir = tempfile.mkdtemp(prefix="gui-demo-chrome-")
    sessions = {"m3a": session, "moving": moving_session, "m3b": m3b_session, "m3b-long": m3b_long_session,
                "m3b-slots": m3b_slots_session}
    try:
        await sessions[mode](run, chrome, profile_dir)
    finally:
        run.stop_all()
        shutil.rmtree(profile_dir, ignore_errors=True)


async def session(run: Run, chrome: str, profile_dir: str) -> None:
    sim = start_simulator(run, SCENARIO_PROFILE, "simulator-1.log")
    await wait_ready(run, sim, "simulator-1.log")
    sim_ready = time.monotonic()

    def scenario_t() -> float:
        return time.monotonic() - sim_ready

    async def until_scenario(t: float) -> None:
        delay = t - scenario_t()
        if delay > 0:
            run.log(f"waiting {delay:.1f} s, until about scenario t = {t:g} s")
            await asyncio.sleep(delay)

    await launch_chrome(run, chrome, profile_dir)
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = DevTools(ws)
            problems: list[str] = []
            cdp.handlers["Runtime.exceptionThrown"] = lambda p: problems.append(
                "exception: " + str(p.get("exceptionDetails", {}).get("text")))
            cdp.handlers["Runtime.consoleAPICalled"] = lambda p: problems.append(
                f"console.{p.get('type')}: " + " ".join(str(a.get("value")) for a in p.get("args", [])))
            cdp.handlers["Log.entryAdded"] = lambda p: problems.append(
                f"log {p['entry'].get('level')} ({p['entry'].get('source')}): {p['entry'].get('text')}")
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("Log.enable")
            await cdp.viewport(*WIDE)

            # a. Loading: hold the page's first API requests, shoot, release them unchanged.
            held: list[str] = []
            cdp.handlers["Fetch.requestPaused"] = lambda p: held.append(p["requestId"])
            await cdp.send("Fetch.enable", patterns=[{"urlPattern": "*/api/v1/*", "requestStage": "Request"}])
            await cdp.send("Page.navigate", url=PAGE)
            deadline = time.monotonic() + 10
            while not held and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)
            run.log(f"holding {len(held)} API request(s) for the loading shot")
            await cdp.shot(run, "a-loading.png")
            for request_id in held:
                await cdp.send("Fetch.continueRequest", requestId=request_id)
            await cdp.send("Fetch.disable")
            cdp.handlers.pop("Fetch.requestPaused")

            # h. Empty: live, before any traffic.
            await cdp.wait_for(f"{CONN} === 'Live'")
            await cdp.wait_for("!document.querySelector('#vehicle .placeholder, #dtcs .placeholder')")
            await asyncio.sleep(1.5)
            await cdp.shot(run, "h1-empty-fresh-start.png")

            # b. Live, with exchanges flowing and the scenario moving the signals.
            traffic = start_traffic(run)
            await until_scenario(24)
            await cdp.shot(run, "b1-live.png")
            await until_scenario(31)
            await cdp.shot(run, "b2-live-7s-later.png")
            # The two supported-PID range requests, 01 20 then 01 00, near the newest row.
            await cdp.wait_for("(function(){var r=document.querySelector('#log-body tr:last-child');"
                               " return !!r && r.textContent.indexOf('supported PIDs 01') >= 0;})()", timeout=10)
            await asyncio.sleep(0.6)
            await cdp.shot(run, "b3-live-supported-pid-ranges.png")

            # d. A filter, then the paused view with held rows counted.
            await cdp.js("(function(){var s=document.getElementById('f-service'); s.value='19';"
                         "s.dispatchEvent(new Event('change', {bubbles: true})); return s.value;})()")
            await asyncio.sleep(1.0)
            await cdp.shot(run, "d1-filter-service-0x19.png")
            await cdp.js("document.getElementById('btn-reset') ? document.getElementById('btn-reset').click() : "
                         "(function(){var s=document.getElementById('f-service'); s.value='all';"
                         "s.dispatchEvent(new Event('change', {bubbles: true}));})()")
            await cdp.js("document.getElementById('btn-pause').click()")
            await asyncio.sleep(5.0)
            await cdp.shot(run, "d2-paused-held-rows.png")
            await cdp.js("document.getElementById('btn-pause').click()")
            # After the filter reset, pause and resume, the log still follows the newest rows.
            await asyncio.sleep(3.0)
            await cdp.shot(run, "d3-resumed-log-following.png")
            # j. The jump control at 1440: a real wheel scroll up, then a real click on it.
            await jump_pair(run, cdp, "1440")

            # c. The DTC panel: P0128 pending at t = 40 s; confirmed with the MIL at t = 75 s.
            await until_scenario(44)
            await cdp.shot(run, "c1-dtc-pending.png")
            await until_scenario(80)
            await cdp.shot(run, "c2-dtc-confirmed-mil.png")

            # g. One narrow width, the whole page.
            await cdp.viewport(*NARROW)
            await asyncio.sleep(1.5)
            await check_overflow(run, cdp, "M3a 390")
            await cdp.shot(run, "g1-narrow-390.png")
            await cdp.shot(run, "g2-narrow-390-full-page.png", full_page=True)
            await jump_pair(run, cdp, "390")
            # The owner's own desktop width.
            await cdp.viewport(*OWNER_WIDE)
            await cdp.js("window.scrollTo(0, 0)")
            await asyncio.sleep(1.5)
            await check_overflow(run, cdp, "M3a 2000")
            await cdp.shot(run, "w-desktop-2000x1100.png")
            await jump_pair(run, cdp, "2000")
            await cdp.viewport(*WIDE)
            await asyncio.sleep(1.0)
            await check_overflow(run, cdp, "M3a 1440")

            # e. Disconnected: stop the traffic, then the simulator (SIGTERM).
            run.log("stop traffic, then SIGTERM the simulator")
            run.stop(traffic)
            run.stop(sim)
            await cdp.wait_for(f"{CONN} !== 'Live'", timeout=10)
            await asyncio.sleep(4.0)
            await cdp.shot(run, "e-disconnected-stale.png")

            # f. Reconnected after a restart of the same profile.
            sim = start_simulator(run, SCENARIO_PROFILE, "simulator-2.log")
            await wait_ready(run, sim, "simulator-2.log")
            traffic = start_traffic(run)
            await cdp.wait_for(f"{CONN} === 'Live'", timeout=30)
            await asyncio.sleep(3.0)
            # Wheel the log up to the page's own restart marker, as a reader would.
            offset = await cdp.js("(function(){var m=document.querySelectorAll('#log-body tr.mark--link');"
                                  " if (!m.length) return null; var w=document.getElementById('logwrap')"
                                  ".getBoundingClientRect(), r=m[m.length-1].getBoundingClientRect();"
                                  " return r.y + r.height / 2 - (w.y + w.height / 2);})()")
            run.log(f"restart marker offset from the log's centre: {offset}")
            if offset is not None and abs(offset) > 150:
                await cdp.wheel("#logwrap", offset)
            await asyncio.sleep(0.5)
            await cdp.shot(run, "f-reconnected-after-restart.png")

            # h. The no-scenario profile, on a fresh page load with no traffic.
            run.log("stop traffic and simulator; start the no-scenario profile")
            run.stop(traffic)
            run.stop(sim)
            sim = start_simulator(run, DEFAULT_PROFILE, "simulator-3.log")
            await wait_ready(run, sim, "simulator-3.log")
            await cdp.send("Page.reload", ignoreCache=True)
            await asyncio.sleep(1.0)
            await cdp.wait_for(f"{CONN} === 'Live'", timeout=20)
            await asyncio.sleep(2.5)
            await cdp.shot(run, "h2-no-scenario.png")

            errors = await cdp.js("performance.getEntriesByType('resource').map(e => e.name)"
                                  ".filter(n => !n.startsWith('http://127.0.0.1:8765/')).length")
            run.log(f"resources from other origins on the final load: {errors}")
            run.log(f"console messages, exceptions and browser log entries over the whole run: {len(problems)}")
            for problem in problems:
                run.log(f"  {problem}")
            cdp.reader.cancel()
    run.log("done")


# What the vehicle table shows, read from the page: scenario t and the named signals' cells.
VEHICLE_READ = """(function(){
  var out = {meta: document.getElementById('vehicle-meta').textContent};
  document.querySelectorAll('#vehicle tr').forEach(function (tr) {
    var c = tr.querySelectorAll('td, th');
    if (c.length >= 2) {
      var k = c[0].textContent.trim();
      if (['speed', 'rpm', 'coolant_temp', 'odometer', 'throttle', 'engine_load'].indexOf(k) >= 0)
        out[k] = Array.prototype.map.call(c, function (x) { return x.textContent.trim(); }).slice(1).join(' | ');
    }
  });
  return out;
})()"""


def stepped_values(profile: Path) -> dict[str, list[float]]:
    """The profile's `stepped` lists by path, read straight from the YAML."""
    from ruamel.yaml import YAML

    data = YAML(typ="safe").load(profile.read_text())
    return {s["path"]: [float(v) for v in s["values"]]
            for s in data["scenario"]["signals"] if s["type"] == "stepped"}


async def scenario_origin(samples: int = 40) -> float:
    """Wall-clock time at which scenario t was 0: min over polls of (poll time - t_last_applied).

    t_last_applied never runs ahead of the clock, so each sample is an upper bound on the
    origin plus the poll's own latency; the minimum over many polls is the tightest."""
    best = float("inf")
    async with aiohttp.ClientSession() as http:
        for _ in range(samples):
            async with http.get(f"http://{API}/api/v1/status") as resp:
                body = await resp.json()
            now = time.time()
            t = body["scenario"]["t_last_applied"]
            if t is not None:
                best = min(best, now - float(t))
            await asyncio.sleep(0.05)
    return best


async def harvest_exchanges() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after = 0
    async with aiohttp.ClientSession() as http:
        while True:
            async with http.get(f"http://{API}/api/v1/exchanges?after={after}&limit=500") as resp:
                body = await resp.json()
            if not body["events"]:
                return events
            events.extend(body["events"])
            after = body["events"][-1]["seq"]


def obd_crosscheck(run: Run, events: list[dict[str, Any]], origin: float) -> None:
    """Every 01 0C and 01 0D reply against the profile's stepped value for the request's
    scenario second. A request within EDGE_S of a whole second is reported as at an edge:
    the scenario may have been applied on either side of it."""
    edge_s = 0.1
    values = stepped_values(MOVING_PROFILE)
    table = {"010c": ("engine.rpm", lambda b: (b[0] * 256 + b[1]) / 4),
             "010d": ("vehicle.speed", lambda b: float(b[0]))}
    lines, counts = [], {"match": 0, "edge-match": 0, "edge-other-side": 0, "mismatch": 0}
    for e in events:
        key = (e.get("request") or "").replace(" ", "").lower()
        if key not in table or not e.get("response"):
            continue
        path, decode = table[key]
        reply = bytes.fromhex(e["response"].replace(" ", ""))
        got = decode(reply[2:])
        wall = datetime.datetime.fromisoformat(e["t"].replace("Z", "+00:00")).timestamp()
        t = wall - origin
        cycle = values[path]
        step = int(t // 1) if t > 0 else 0
        want = cycle[step % len(cycle)]
        near = abs(t - round(t)) < edge_s
        edge = round(t)
        other = cycle[edge % len(cycle)] if edge > step else cycle[(edge - 1) % len(cycle)]
        if got == want:
            verdict = "edge-match" if near else "match"
        elif near and got == other:
            verdict = "edge-other-side"
        else:
            verdict = "mismatch"
        counts[verdict] += 1
        lines.append(f"seq {e['seq']:4d}  t={t:7.3f}  {key[:2]} {key[2:]}  reply {e['response']:<14s}"
                     f"  {path}={got:g}  expected step {step % len(cycle):2d}: {want:g}  {verdict}")
    (run.outdir / "obd-crosscheck.txt").write_text(
        f"scenario origin (wall, s): {origin:.3f}; edge window: {edge_s} s\n"
        + "\n".join(lines) + f"\n\nsummary: {counts}\n")
    run.log(f"OBD cross-check of 01 0C / 01 0D: {counts}")


async def moving_session(run: Run, chrome: str, profile_dir: str) -> None:
    """The moving-vehicle set: the stepped 90 s drive cycle, one run, shots across a loop."""
    sim = start_simulator(run, MOVING_PROFILE, "simulator-1.log")
    await wait_ready(run, sim, "simulator-1.log")
    origin = await scenario_origin()
    run.log(f"scenario origin estimated at wall {origin:.3f}")

    async def until_scenario(t: float) -> None:
        delay = origin + t - time.time()
        if delay > 0:
            run.log(f"waiting {delay:.1f} s, until scenario t = {t:g} s")
            await asyncio.sleep(delay)

    await launch_chrome(run, chrome, profile_dir)
    traffic = None
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = DevTools(ws)
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.viewport(*WIDE)
            await cdp.send("Page.navigate", url=PAGE)
            await cdp.wait_for(f"{CONN} === 'Live'")
            await cdp.wait_for("!document.querySelector('#vehicle .placeholder')")
            traffic = start_traffic(run)

            async def shot(name: str) -> None:
                await cdp.shot(run, name)
                run.log(f"  {name} vehicle table: {await cdp.js(VEHICLE_READ)}")

            await until_scenario(3.5)
            await check_overflow(run, cdp, "moving 1440")
            await shot("m-idle-odometer-unavailable.png")
            await until_scenario(10.5)
            await shot("m-accelerating.png")
            await until_scenario(40.5)
            await shot("m-cruising.png")
            await cdp.viewport(*OWNER_WIDE)
            await until_scenario(45.5)
            await check_overflow(run, cdp, "moving 2000")
            await shot("m-2000-cruising.png")
            await cdp.viewport(*NARROW)
            await cdp.js("document.getElementById('vehicle-panel').scrollIntoView({block: 'start'})")
            await until_scenario(50.5)
            await check_overflow(run, cdp, "moving 390")
            await shot("m-390-vehicle.png")
            await cdp.js("window.scrollTo(0, 0)")
            await cdp.viewport(*WIDE)
            await until_scenario(65.5)
            await shot("m-braking.png")
            await until_scenario(92.5)
            await shot("m-after-loop-idle.png")

            events = await harvest_exchanges()
            obd_crosscheck(run, events, origin)

            run.log("stop traffic, then SIGTERM the simulator")
            run.stop(traffic)
            run.stop(sim)
            await cdp.wait_for(f"{CONN} !== 'Live'", timeout=10)
            await asyncio.sleep(4.0)
            await check_overflow(run, cdp, "moving 1440, stale")
            await shot("m-disconnected-stale.png")
            cdp.reader.cancel()
    run.log("done")


# ---------- M3b: the browser checks of design §12.2 and the cost measurement of §11.3 ----------
# Every check reads only what §12.2 allows: the visible readouts and table cells as text, the
# connection text, the banner and the panel tags, and the read-only data-* attributes the page
# writes on <body> and on each graph. The page is the shipped page, unchanged. The only
# instrumentation is WS_WRAPPER below, added with Page.addScriptToEvaluateOnNewDocument before
# the page's scripts run: it wraps window.WebSocket (to count the state frames received and to
# dispatch synthetic MessageEvents on the page's socket) and window.fetch (to time-stamp each
# GET /status answer and its state_encoding.ok), and records everything in window.__m3b.

FAULT_SERVER = ROOT / "scripts" / "gui_fault_server.py"
EVENTS_URL = f"ws://{API}/api/v1/events"
CAP = 4096

WS_WRAPPER = r"""(function () {
  var Native = window.WebSocket, nativeFetch = window.fetch;
  var T = window.__m3b = { socks: [], states: [], status: [], badFirst: false };
  var live = [];
  function bad(i) {
    var ev = new MessageEvent("message", { data: "{bad" }); ev.__synthetic = true;
    T.socks[i].bad = Date.now();
    live[i].dispatchEvent(ev);
  }
  function W(url, protocols) {
    var ws = protocols === undefined ? new Native(url) : new Native(url, protocols);
    var i = T.socks.length;
    var rec = { url: String(url), created: Date.now(), opened: null, error: null, closed: null, code: null,
                frames: 0, states: 0, bad: null };
    T.socks.push(rec); live.push(ws);
    ws.addEventListener("open", function () { rec.opened = Date.now(); });
    ws.addEventListener("error", function () { rec.error = Date.now(); });
    ws.addEventListener("close", function (e) { rec.closed = Date.now(); rec.code = e.code; });
    // Registered before the page's own handler, so it sees every frame first.
    ws.addEventListener("message", function (e) {
      if (e.__synthetic) return;
      rec.frames += 1;
      if (rec.frames === 1 && T.badFirst) { e.stopImmediatePropagation(); bad(i); return; }
      try {
        var m = JSON.parse(e.data);
        if (m && m.type === "state") { rec.states += 1; T.states.push({ t: Date.now(), sock: i, len: e.data.length }); }
      } catch (x) { /* not ours to judge */ }
    });
    var close = ws.close;
    ws.close = function (code) { rec.closeCalled = Date.now(); return close.apply(ws, arguments); };
    return ws;
  }
  W.prototype = Native.prototype;
  W.CONNECTING = 0; W.OPEN = 1; W.CLOSING = 2; W.CLOSED = 3;
  window.WebSocket = W;
  T.bad = function () { bad(T.socks.length - 1); };
  // Every change of the body's health attributes, time-stamped: a state can last a few ms
  // (an attempt that succeeds at once), too short for any sampling to see.
  T.attrs = [];
  var last = {};
  new MutationObserver(function (list) {
    list.forEach(function (m) {
      if (m.target !== document.body) return;
      var a = m.attributeName.slice(5), v = document.body.getAttribute(m.attributeName);
      if (last[a] !== v) { last[a] = v; T.attrs.push({ t: Date.now(), a: a, v: v }); }
    });
  }).observe(document, { subtree: true, attributes: true,
    attributeFilter: ["data-attempts", "data-timers", "data-episode", "data-health", "data-conn"] });
  window.fetch = function (input) {
    var url = typeof input === "string" ? input : input.url;
    var p = nativeFetch.apply(this, arguments);
    if (url.indexOf("/status") >= 0) {
      var rec = { start: Date.now(), end: null, http: null, ok: null, started_at: null, failed: null };
      T.status.push(rec);
      p.then(function (r) {
        rec.http = r.status;
        r.clone().json().then(function (b) {
          rec.end = Date.now();
          rec.ok = b.api && b.api.state_encoding ? b.api.state_encoding.ok : null;
          rec.failed = b.api ? b.api.state_encode_failed : null;
          rec.started_at = b.started_at;
        }, function () { rec.end = Date.now(); });
      }, function () { rec.end = Date.now(); rec.http = "error"; });
    }
    return p;
  };
})();"""

# The wrapper's record of the body's attribute changes since a page time (ms).
ATTRS_SINCE = "window.__m3b.attrs.filter(x => x.t >= %d)"

# The page's condition, its graphs and the wrapper's counts, in one evaluation.
PAGE_STATE = """(() => {
  const d = document.body.dataset, ls = document.getElementById('linkstate');
  const vis = sel => [...document.querySelectorAll(sel)].filter(t => !t.hidden).map(t => t.textContent);
  const T = window.__m3b || {socks: [], states: [], status: []};
  const graphs = {};
  document.querySelectorAll('#graphs-grid figure.graph').forEach(f => {
    graphs[f.dataset.path] = Object.assign({}, f.dataset, {hidden: f.hidden,
      line1: f.querySelector('.graph__value').textContent, line2: f.querySelector('.graph__line2').textContent});
  });
  const cells = {};
  document.querySelectorAll('#vehicle tr').forEach(tr => {
    const th = tr.querySelector('th.sig__name'), td = tr.querySelector('td.num');
    if (th && td && graphs[th.title]) cells[th.title] = td.textContent;
  });
  const b = document.getElementById('btn-retry'), note = document.getElementById('graphs-note');
  // The one shared notice line under the head: the restart note (Task 37) and the gap notes.
  const brk = document.getElementById('graphs-breaks');
  return {now: Date.now(), conn: d.conn, data: d.data, reason: d.reason, health: d.health, episode: d.episode,
    attempts: Number(d.attempts), timers: Number(d.timers), polls: Number(d.polls), malformed: Number(d.malformedTotal),
    text: document.getElementById('conn-text').textContent, banner: ls.hidden ? null : ls.innerText,
    retry: !ls.hidden && b && !b.hidden ? b.textContent : null, known: vis('.known-tag'), stale: vis('.stale-tag'),
    socks: T.socks.length, states: T.states.length, graphs: graphs, cells: cells,
    note: note.hidden ? '' : note.textContent, breaks: brk && !brk.hidden ? brk.textContent : ''};
})()"""

WRAPPER_READ = ("JSON.parse(JSON.stringify({socks: window.__m3b.socks, states: window.__m3b.states,"
                " status: window.__m3b.status}))")

# Line 1 of each shown graph against the signal table's value cell, in one evaluation.
AGREE = """(() => {
  const rows = {};
  document.querySelectorAll('#vehicle tr').forEach(tr => {
    const th = tr.querySelector('th.sig__name'), td = tr.querySelector('td.num');
    if (th && td) rows[th.title] = td.textContent;
  });
  const res = [];
  document.querySelectorAll('#graphs-grid figure.graph').forEach(f => {
    if (f.hidden) return;
    res.push([f.dataset.path, f.querySelector('.graph__value').textContent, rows[f.dataset.path]]);
  });
  return {health: document.body.dataset.health, pairs: res,
          paused: [...document.querySelectorAll('#graphs-grid figure.graph')].some(f => f.dataset.paused === 'true')};
})()"""

LOG_READ = """(() => {
  const rows = [...document.querySelectorAll('#log-body > tr')];
  const seqs = rows.map(r => r.querySelector('td.c-seq')).filter(Boolean).map(td => Number(td.textContent));
  return {text: document.getElementById('log-body').innerText, seqs: seqs,
          count: document.getElementById('log-count').textContent,
          gaps: rows.filter(r => r.classList.contains('mark--gap')).length,
          marks: rows.filter(r => r.classList.contains('mark')).map(r => r.textContent.slice(0, 100))};
})()"""

# Full log rows inside #logwrap, below its sticky header, at the current viewport.
LOG_ROWS = """(() => {
  const w = document.getElementById('logwrap'), wr = w.getBoundingClientRect();
  const th = w.querySelector('thead th');
  const top = th && th.getBoundingClientRect().height ? Math.max(wr.top, th.getBoundingClientRect().bottom) : wr.top;
  let full = 0, fullExchange = 0;
  const heights = [];
  document.querySelectorAll('#log-body > tr').forEach(r => {
    const b = r.getBoundingClientRect();
    if (b.height === 0) return;
    if (b.top >= top - 0.5 && b.bottom <= wr.bottom + 0.5) {
      full++; heights.push(Math.round(b.height));
      if (r.querySelector('td.c-seq')) fullExchange++;        // an exchange row, not a marker row
    }
  });
  const gp = document.getElementById('graphs-panel').getBoundingClientRect();
  const plot = document.querySelector('.graph__plot');
  const card = document.querySelector('figure.graph:not([hidden])');
  // Clearance: the rows region minus what the 5 newest full rows take (negative: 5 do not fit).
  const five = heights.slice(-5), need5 = five.length === 5 ? five.reduce((a, h) => a + h, 0) : null;
  const bl = document.getElementById('graphs-breaks'), blH = bl.getBoundingClientRect().height;
  const blLine = parseFloat(getComputedStyle(bl).lineHeight);
  return {full: full, fullExchange: fullExchange, heights: heights,
          need5: need5, clearance5: need5 === null ? null : Math.round(wr.bottom - top) - need5,
          breaksLines: blH && blLine ? Math.round(blH / blLine) : 0,
          rowsRegion: Math.round(wr.bottom - top), graphsH: Math.round(gp.height),
          plotH: plot ? plot.getBoundingClientRect().height : null,
          cardH: card ? card.getBoundingClientRect().height : null,
          banner: !document.getElementById('linkstate').hidden,
          line2: [...document.querySelectorAll('figure.graph:not([hidden]) .graph__line2')].map(e => e.textContent),
          breaks: (b => b && !b.hidden ? b.textContent : '')(document.getElementById('graphs-breaks')),
          statusH: Math.round(document.querySelector('header').getBoundingClientRect().height),
          note: (n => n && !n.hidden ? n.textContent : '')(document.getElementById('graphs-note')),
          // The parts of the second column, to explain a difference between two readings.
          parts: {graphsHead: Math.round(document.querySelector('.graphs__head').getBoundingClientRect().height),
                  breaksLine: Math.round(document.getElementById('graphs-breaks').getBoundingClientRect().height),
                  noteLine: Math.round(document.getElementById('graphs-note').getBoundingClientRect().height),
                  logwrap: Math.round(wr.height),
                  logPanelTop: Math.round(w.closest('section').getBoundingClientRect().top),
                  logwrapTop: Math.round(wr.top), headerBottom: Math.round(top)}};
})()"""

CANVASES = """(() => ({dpr: window.devicePixelRatio,
  canvases: [...document.querySelectorAll('#graphs .uplot canvas')].map(c => ({w: c.width, h: c.height,
    cssW: c.clientWidth, cssH: c.clientHeight, path: c.closest('figure').dataset.path}))}))()"""

HEAP = ("(() => ({t: Date.now(), used: performance.memory.usedJSHeapSize, total: performance.memory.totalJSHeapSize,"
        " limit: performance.memory.jsHeapSizeLimit}))()")

GAP_NOTE = r"No data from t = [0-9.]+ to t = [0-9.]+ s \(([^)]*)\)"


class Cases:
    """The per-case results: each with its pass rule, its observed values and pass or fail."""

    def __init__(self, run: Run) -> None:
        self.run = run
        self.items: list[dict[str, Any]] = []
        self.agreement: list[dict[str, Any]] = []
        self.cost: list[dict[str, Any]] = []
        self.extra: dict[str, Any] = {}

    def record(self, case: str, rule: str, conds: dict[str, bool], observed: dict[str, Any]) -> bool:
        passed = all(conds.values())
        failed = [k for k, v in conds.items() if not v]
        self.items.append({"case": case, "rule": rule, "pass": passed, "failed": failed, "conditions": conds,
                           "observed": observed})
        self.run.log(f"{'PASS' if passed else 'FAIL'} {case}" + (f"  failed: {failed}" if failed else ""))
        self.run.log(f"  observed: {json.dumps(observed, default=str)[:1500]}")
        if not passed:
            self.run.case_failures.append(case)
        return passed

    def write(self, chrome_version: str) -> None:
        live = [a for a in self.agreement if a["checked"]]
        self.record("Agreement", "Every live screenshot with the graphs not paused (data-paused false): each line-1 "
                    "value equals the signal table's cell, read in one evaluation",
                    {"every live screenshot with graphs agrees": all(a["agree"] for a in live),
                     "at least one live screenshot had graphs": bool(live)},
                    {"checked": len(live), "skipped": [a["shot"] for a in self.agreement if not a["checked"]],
                     "paused_not_checked": [a["shot"] for a in self.agreement if a["paused"]],
                     "disagreeing": [a for a in live if not a["agree"]]})
        passed = sum(1 for c in self.items if c["pass"])
        out = {"chrome": chrome_version, "cases": self.items, "agreement": self.agreement, "cost": self.cost,
               "extra": self.extra, "tally": {"passed": passed, "total": len(self.items)}}
        (self.run.outdir / "m3b-results.json").write_text(json.dumps(out, indent=1, default=str))
        self.run.log(f"M3b cases: {passed} of {len(self.items)} passed")


class M3b:
    """One Chrome page with the wrapper, and the helpers every case uses."""

    def __init__(self, run: Run, cdp: DevTools, http: aiohttp.ClientSession, cases: Cases) -> None:
        self.run, self.cdp, self.http, self.cases = run, cdp, http, cases

    async def state(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.cdp.js(PAGE_STATE)
        return result

    async def wrapper(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.cdp.js(WRAPPER_READ)
        return result

    async def settled(self) -> None:
        """Two animation frames: the layout and draws queued by the last change have run."""
        await self.cdp.send("Runtime.evaluate", awaitPromise=True,
                            expression="new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

    async def attrs(self, since_ms: float) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self.cdp.js(ATTRS_SINCE % since_ms)
        return result

    async def status(self) -> dict[str, Any]:
        async with self.http.get(f"http://{API}/api/v1/status") as resp:
            body: dict[str, Any] = await resp.json()
            return body

    async def open_page(self, size: tuple[int, int] = WIDE) -> None:
        await self.cdp.viewport(*size)
        await self.cdp.send("Page.navigate", url=PAGE)
        await self.cdp.wait_for("document.body && document.body.dataset.health === 'live'", timeout=30)

    async def until(self, predicate: Callable[[dict[str, Any]], bool], timeout: float,
                    period: float = 0.1) -> tuple[list[dict[str, Any]], bool]:
        """Sample every ``period`` until ``predicate`` holds or ``timeout`` passes."""
        samples: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        while True:
            s = await self.state()
            samples.append(s)
            if predicate(s):
                return samples, True
            if time.monotonic() >= deadline:
                return samples, False
            await asyncio.sleep(period)

    async def until_as_of(self, t: float, timeout: float = 120.0) -> None:
        expr = ("(() => { const f = document.querySelector('#graphs-grid figure.graph');"
                " return f && f.dataset.asOf !== undefined && f.dataset.asOf !== ''"
                " ? Number(f.dataset.asOf) : -1; })()")
        deadline = time.monotonic() + timeout
        while (now := float(await self.cdp.js(expr))) < t:
            if time.monotonic() > deadline:
                raise RuntimeError(f"scenario t did not reach {t} (at {now})")
            await asyncio.sleep(0.1)

    async def shot(self, name: str) -> None:
        """A screenshot, then the agreement check in one evaluation (for every live shot)."""
        await self.cdp.shot(self.run, name)
        a = await self.cdp.js(AGREE)
        # Agreement covers live shots taken with the graphs not paused (data-paused false; controller
        # ruling). While paused, line 1 is frozen at data-drawn-to and the table keeps updating (§6.9),
        # so a paused shot is the Pause case's business, not this one's; it is listed, not checked.
        checked = a["health"] == "live" and bool(a["pairs"]) and not a["paused"]
        agree = all(p[1] == p[2] for p in a["pairs"])
        self.cases.agreement.append({"shot": name, "health": a["health"], "checked": checked, "agree": agree,
                                     "paused": a["paused"], "pairs": a["pairs"]})
        if checked:
            self.run.log(f"  agreement {'ok' if agree else 'MISMATCH'}: {a['pairs']}")

    async def click_window(self, seconds: int) -> None:
        await self.cdp.click(f'#graphs-panel [data-window="{seconds}"]')
        await asyncio.sleep(0.4)

    async def log(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.cdp.js(LOG_READ)
        return result


def seq_continuous(seqs: list[int]) -> bool:
    """No duplicate seq, and each row's seq is the previous one plus 1."""
    return len(set(seqs)) == len(seqs) and all(b - a == 1 for a, b in itertools.pairwise(seqs))


def slim(s: dict[str, Any], paths: tuple[str, ...] = ()) -> dict[str, Any]:
    """A sample without its graphs (or with only the named ones), for the results file."""
    out = {k: v for k, v in s.items() if k != "graphs"}
    if paths:
        out["graphs"] = {p: s["graphs"].get(p) for p in paths}
    return out


def graph_vals(s: dict[str, Any], key: str) -> dict[str, Any]:
    return {p: g.get(key) for p, g in s["graphs"].items() if not g["hidden"]}


class ScriptClients:
    """WebSocket clients of the capture script's own, each drained so it never backs up."""

    def __init__(self, http: aiohttp.ClientSession) -> None:
        self.http = http
        self.held: list[tuple[aiohttp.ClientWebSocketResponse, asyncio.Task[None]]] = []

    async def open(self) -> None:
        ws = await self.http.ws_connect(EVENTS_URL, headers={"Origin": f"http://{API}"})

        async def drain() -> None:
            async for _ in ws:
                pass

        self.held.append((ws, asyncio.create_task(drain())))

    async def close_last(self) -> None:
        ws, task = self.held.pop()
        await ws.close()
        task.cancel()

    async def close_all(self) -> None:
        while self.held:
            await self.close_last()


async def m3b_start(run: Run, profile: Path, logname: str, faults: list[str] | None = None) -> subprocess.Popen[bytes]:
    """The real simulator, or with ``faults`` the fault-injection test server (§12.3)."""
    if faults is None:
        proc = start_simulator(run, profile, logname)
    else:
        run.log(f"start fault server: --profile {profile.relative_to(ROOT)} {' '.join(faults)}")
        proc = run.spawn([sys.executable, str(FAULT_SERVER), "--profile", str(profile), "--interface", IFACE,
                          "--api", API, *faults], logname)
    await wait_ready(run, proc, logname)
    return proc


async def m3b_cdp(run: Run, http: aiohttp.ClientSession, ws: aiohttp.ClientWebSocketResponse,
                  problems: list[str]) -> DevTools:
    cdp = DevTools(ws)
    cdp.handlers["Runtime.exceptionThrown"] = lambda p: problems.append(
        "exception: " + str(p.get("exceptionDetails", {}).get("text")))
    cdp.handlers["Runtime.consoleAPICalled"] = lambda p: problems.append(
        f"console.{p.get('type')}: " + " ".join(str(a.get("value")) for a in p.get("args", [])))
    cdp.handlers["Log.entryAdded"] = lambda p: problems.append(
        f"log {p['entry'].get('level')} ({p['entry'].get('source')}): {p['entry'].get('text')}")
    await cdp.send("Page.enable")
    await cdp.send("Runtime.enable")
    await cdp.send("Log.enable")
    await cdp.send("Page.addScriptToEvaluateOnNewDocument", source=WS_WRAPPER)
    return cdp


async def chrome_version(http: aiohttp.ClientSession) -> str:
    async with http.get(f"http://127.0.0.1:{DEVTOOLS_PORT}/json/version") as resp:
        body = await resp.json()
    return str(body.get("Browser"))


# ---- part A: the real simulator, ice_default.yaml (no scenario), no traffic ----

async def case_no_false_invalidation(m: M3b) -> None:
    await m.open_page()
    start = await m.state()
    samples = [start]
    for _ in range(13):                       # 0, 5, ..., 65 s
        await asyncio.sleep(5.0)
        samples.append(await m.state())
    w = await m.wrapper()

    def healthy(s: dict[str, Any]) -> bool:
        return (s["text"] == "Live" and s["conn"] == "live" and s["data"] == "current" and s["health"] == "live"
                and not s["known"] and not s["stale"] and s["banner"] is None and s["malformed"] == 0
                and s["episode"] == "none")

    rose = samples[-1]["polls"] - start["polls"]
    span = (samples[-1]["now"] - start["now"]) / 1000
    m.cases.record(
        "No false invalidation",
        "At every 5 s sample over 65 s: conn text Live; data-conn live, data-data current, data-health live; no "
        "Last known or Stale tag; no banner; data-malformed-total 0; data-episode none; data-polls rose by about 30; "
        "exactly one state received in the whole period",
        {"every sample healthy": all(healthy(s) for s in samples),
         "polls rose by about 30 (one per 2 s, 26-36)": 26 <= rose <= 36,
         "exactly one state since the page loaded": len(w["states"]) == 1},
        {"samples": [slim(s) for s in samples], "span_s": span, "polls_rose": rose, "states": w["states"],
         "sockets": len(w["socks"])})
    await m.shot("m3b-a1-no-scenario-65s.png")


async def case_malformed_once(m: M3b) -> None:
    before = await m.status()
    w0 = await m.wrapper()
    imm = await m.cdp.js("(() => { window.__m3b.bad(); return " + PAGE_STATE + "; })()")
    t_bad = imm["now"]
    samples, ok = await m.until(lambda s: s["health"] == "live", timeout=6.0)
    t_live = samples[-1]["now"]
    await asyncio.sleep(0.6)                   # the log re-renders at most every 200 ms
    end = await m.state()
    lg = await m.log()
    after = await m.status()
    w = await m.wrapper()
    new_socks = w["socks"][len(w0["socks"]):]
    m.cases.record(
        "Malformed state, then unchanged data",
        "Immediately: data-conn live, data-data last-known, data-reason malformed, the Last known tag, "
        "data-malformed-total 1, data-episode active. Within 3 s: the resync reconnected, hello arrived and the state "
        "after it was applied; data-health live, data-episode none, data-attempts 0; the tag gone; the log shows "
        "Resynchronised after an unreadable message; started_at unchanged; no restart marker; seq continuity",
        {"immediately last-known/malformed/active": (imm["conn"], imm["data"], imm["reason"], imm["episode"],
                                                    imm["malformed"]) == ("live", "last-known", "malformed",
                                                                          "active", 1),
         "immediately the Last known tag": any(t.startswith("Last known") for t in imm["known"]),
         "live within 3 s": ok and (t_live - t_bad) / 1000 <= 3.0,
         "a new socket, its hello and its state": bool(new_socks) and new_socks[-1]["frames"] >= 2
         and new_socks[-1]["states"] >= 1,
         "after: live, episode none, attempts 0, no tag": (end["health"], end["episode"], end["attempts"],
                                                          end["known"]) == ("live", "none", 0, []),
         "log has the resync line": "Resynchronised after an unreadable message" in lg["text"],
         "no restart marker": "Simulator restarted" not in lg["text"],
         "started_at unchanged": before["started_at"] == after["started_at"],
         # No traffic in part A, so there is no exchange row: continuity holds vacuously, and says so.
         ("seq continuity" if lg["seqs"] else "seq continuity (vacuous: no exchanges, no traffic)"):
         seq_continuous(lg["seqs"]) and "duplicate" not in lg["count"]},
        {"seq_continuity": "checked" if lg["seqs"] else "vacuous: no exchanges (no traffic)",
         "immediately": slim(imm), "live_after_s": (t_live - t_bad) / 1000, "end": slim(end),
         "transitions": [slim(s) for s in samples if s is samples[0] or s is samples[-1]],
         "new_sockets": new_socks, "log_marks": lg["marks"], "seqs": lg["seqs"][-20:],
         "started_at": [before["started_at"], after["started_at"]]})
    await m.shot("m3b-a2-malformed-recovered.png")


async def case_malformed_bounded(m: M3b) -> None:
    w0 = await m.wrapper()
    n0 = len(w0["socks"])
    await m.cdp.js("window.__m3b.badFirst = true")
    imm = await m.cdp.js("(() => { window.__m3b.bad(); return " + PAGE_STATE + "; })()")
    samples, exhausted = await m.until(lambda s: s["episode"] == "exhausted", timeout=15.0)
    at_exhaustion = samples[-1]
    await m.shot("m3b-a3-malformed-exhausted.png")
    quiet: list[dict[str, Any]] = []
    t_end = time.monotonic() + 30.0
    while time.monotonic() < t_end:
        quiet.append(await m.state())
        await asyncio.sleep(0.1)
    w = await m.wrapper()
    socks = w["socks"]
    attempts = socks[n0:]
    ends = [socks[n0 - 1]["bad"]] + [a["bad"] for a in attempts]
    waits = [round((a["created"] - e) / 1000, 3) for a, e in zip(attempts, ends, strict=False)]
    allsamples = samples + quiet
    changes = await m.attrs(imm["now"])
    # Retry now, with the fault removed: a fresh budget.
    await m.cdp.js("window.__m3b.badFirst = false")
    t_click = (await m.state())["now"]
    await m.cdp.click("#btn-retry")
    retry, recovered = await m.until(lambda s: s["health"] == "live", timeout=12.0)
    retry_changes = await m.attrs(t_click)
    retry_attempts = [int(x["v"]) for x in retry_changes if x["a"] == "attempts"]
    m.cases.record(
        "Malformed, bounded",
        "Exactly 3 attempts (data-attempts 3), started 1, 2 and 4 s after the previous one ended (+-0.3 s, from the "
        "wrapper's socket timestamps); then data-episode exhausted, Could not recover ... with Retry now. No further "
        "socket in the following 30 s. data-timers <= 1 at every 100 ms sample; data-health never live meanwhile. "
        "Removing the fault and pressing Retry now recovers with a fresh budget",
        {"immediately active": imm["episode"] == "active",
         "exactly 3 attempts": len(attempts) == 3 and at_exhaustion["attempts"] == 3,
         "waits 1, 2, 4 s (+-0.3)": len(waits) == 3 and all(abs(x - y) <= 0.3 for x, y in zip(waits, (1, 2, 4),
                                                                                              strict=True)),
         "exhausted, Could not recover, Retry now": exhausted and (at_exhaustion["banner"] or "").startswith(
             "Could not recover") and at_exhaustion["retry"] == "Retry now",
         "no further socket in 30 s": len(socks) == n0 + 3 and quiet[-1]["socks"] == at_exhaustion["socks"],
         "data-timers <= 1 at every sample and every change": all(s["timers"] <= 1 for s in allsamples)
         and all(int(x["v"]) <= 1 for x in changes if x["a"] == "timers"),
         "never live meanwhile": all(s["health"] != "live" for s in allsamples)
         and not any(x["a"] == "health" and x["v"] == "live" for x in changes),
         "data-attempts went 1, 2, 3": [int(x["v"]) for x in changes if x["a"] == "attempts"] == [1, 2, 3],
         # A fresh budget: the new episode's first attempt is number 1, and it recovers.
         "Retry now recovers with a fresh budget": recovered and retry[-1]["episode"] == "none"
         and retry[-1]["attempts"] == 0 and next((v for v in retry_attempts if v), None) == 1},
        {"waits_s": waits, "attempt_sockets": attempts, "at_exhaustion": slim(at_exhaustion),
         "samples": len(allsamples), "max_timers": max(s["timers"] for s in allsamples),
         "sockets_at_exhaustion": at_exhaustion["socks"], "sockets_after_30s": quiet[-1]["socks"],
         "attribute_changes": changes, "retry_attribute_changes": retry_changes, "after_retry": slim(retry[-1])})


async def case_slots(m: M3b, clients: ScriptClients, variant: str) -> None:
    """§12.2, recovery with all four client slots occupied. A leaves the freed slot alone; B takes it."""
    st = await m.status()
    refused0 = st["api"]["refused_clients"]
    w0 = await m.wrapper()
    n0 = len(w0["socks"])
    imm = await m.cdp.js("(() => { window.__m3b.bad(); return " + PAGE_STATE + "; })()")
    setup: dict[str, Any] = {}
    if variant == "B":
        # As soon as the page's old socket is gone from the server, a fourth script client takes the slot.
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3.0:
            if (await m.status())["api"]["clients"] <= 3:
                break
            await asyncio.sleep(0.02)
        await clients.open()
        setup = {"slot_taken_after_s": round(time.monotonic() - t0, 3),
                 "clients_now": (await m.status())["api"]["clients"],
                 "page_sockets_open": sum(1 for s in (await m.wrapper())["socks"][n0:] if s["opened"])}
    done = (lambda s: s["health"] == "live") if variant == "A" else (lambda s: s["episode"] == "exhausted")
    samples, reached = await m.until(done, timeout=15.0)
    quiet: list[dict[str, Any]] = []
    refused_series: list[int] = []
    if variant == "B":
        refused_at_exhaustion = (await m.status())["api"]["refused_clients"]
        t_end = time.monotonic() + 30.0
        k = 0
        while time.monotonic() < t_end:
            quiet.append(await m.state())
            if k % 20 == 0:
                refused_series.append((await m.status())["api"]["refused_clients"])
            k += 1
            await asyncio.sleep(0.1)
        refused_series.append((await m.status())["api"]["refused_clients"])
    w = await m.wrapper()
    attempts = w["socks"][n0:]
    starts = [a["created"] for a in attempts]
    gaps = [round((b - a) / 1000, 3) for a, b in itertools.pairwise(starts)]
    refused_now = (await m.status())["api"]["refused_clients"]
    not_opened = sum(1 for a in attempts if a["opened"] is None)
    allsamples = samples + quiet
    changes = await m.attrs(imm["now"])
    # data-attempts as it changed (the wrapper's attribute log), from the value at the fault.
    seen = [imm["attempts"]] + [int(x["v"]) for x in changes if x["a"] == "attempts"]
    steps = [b - a for a, b in itertools.pairwise(seen)]
    conds = {
        "data-timers <= 1 at every 100 ms sample and every change": all(s["timers"] <= 1 for s in allsamples)
        and all(int(x["v"]) <= 1 for x in changes if x["a"] == "timers"),
        "no two attempts start less than 0.9 s apart": all(g >= 0.9 for g in gaps),
        "each attempt raises data-attempts by exactly 1": all(x == 1 for x in steps if x > 0)
        and sum(1 for x in steps if x > 0) == len(attempts),
        "each 503 counted in refused_clients, one per attempt": refused_now - refused0 == not_opened,
    }
    observed: dict[str, Any] = {"immediately": slim(imm), "attempt_sockets": attempts, "start_gaps_s": gaps,
                                "attempts_seen": seen, "refused_before": refused0, "attribute_changes": changes,
                                "refused_after": refused_now, "attempts_not_opened": not_opened,
                                "end": slim(allsamples[-1]), "setup": setup}
    if variant == "A":
        conds["recovered within the budget, then live and the budget reset"] = (
            reached and samples[-1]["episode"] == "none" and samples[-1]["attempts"] == 0
            and len(attempts) <= 3)
        observed["live_after_s"] = (samples[-1]["now"] - imm["now"]) / 1000
    else:
        conds["setup: the script holds the freed slot before the page's first attempt"] = (
            setup.get("clients_now") == 4 and setup.get("page_sockets_open") == 0)
        conds["exactly 3 attempts, then exhausted and Retry now"] = (
            reached and len(attempts) == 3 and samples[-1]["attempts"] == 3
            and samples[-1]["retry"] == "Retry now")
        conds["no further upgrade request in 30 s (refused_clients stops rising)"] = (
            len(set(refused_series)) == 1 and refused_series[0] == refused_at_exhaustion
            and quiet[-1]["socks"] == samples[-1]["socks"])
        observed["refused_series_30s"] = refused_series
        await m.shot("m3b-a5-slots-b-exhausted.png")
        await clients.close_last()
        t0 = time.monotonic()
        while (await m.status())["api"]["clients"] > 3 and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.05)
        await m.cdp.click("#btn-retry")
        retry, recovered = await m.until(lambda s: s["health"] == "live", timeout=12.0)
        conds["releasing the slot and pressing Retry now recovers"] = (
            recovered and retry[-1]["episode"] == "none" and retry[-1]["attempts"] == 0)
        observed["after_retry"] = slim(retry[-1])
    m.cases.record(
        f"Recovery with all four client slots occupied, variant {variant}",
        "Both: data-timers <= 1 at every 100 ms sample; no two attempts start less than 0.9 s apart; each attempt "
        "raises data-attempts by exactly 1; any 503 is counted in refused_clients and matches one attempt. A: "
        "recovery within the budget, then data-health live and the budget reset. B: exactly 3 attempts, then "
        "exhausted and Retry now, with no further upgrade request in 30 s (refused_clients stops rising). Releasing "
        "the slot and pressing Retry now recovers", conds, observed)


async def case_slots_c(m: M3b, clients: ScriptClients) -> None:
    """Variant C (Task 38): a deterministic 503, then automatic recovery inside the same episode.

    The script holds the fourth slot as soon as the page's old socket has left the server, so the
    page's first attempt is refused for certain; it releases the slot only once the server's
    refused_clients has risen (that refusal observed, not assumed). The page's next scheduled
    attempt must then recover on its own: "Retry now" is never pressed. A second malformed frame
    afterwards must start a fresh episode at attempt 1, which shows the budget was reset.
    """
    refused0 = (await m.status())["api"]["refused_clients"]
    n0 = len((await m.wrapper())["socks"])
    imm = await m.cdp.js("(() => { window.__m3b.bad(); return " + PAGE_STATE + "; })()")
    # 1. Take the slot the moment the page's old socket is gone from the server.
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.0 and (await m.status())["api"]["clients"] > 3:
        await asyncio.sleep(0.02)
    await clients.open()
    held = await m.status()
    setup = {"slot_taken_after_s": round(time.monotonic() - t0, 3), "clients_now": held["api"]["clients"],
             "page_sockets_open": sum(1 for x in (await m.wrapper())["socks"][n0:] if x["opened"])}
    # 2. Wait for the refusal itself: refused_clients rises (the page's first attempt got 503).
    refused_series = [refused0]
    t1 = time.monotonic()
    while time.monotonic() - t1 < 8.0:
        r = (await m.status())["api"]["refused_clients"]
        if r != refused_series[-1]:
            refused_series.append(r)
        if r > refused0:
            break
        await asyncio.sleep(0.05)
    t_refused = time.time() * 1000
    # 3. Release the slot, and confirm the server has freed it.
    await clients.close_last()
    while (await m.status())["api"]["clients"] > 3 and time.monotonic() - t1 < 10.0:
        await asyncio.sleep(0.02)
    t_released = time.time() * 1000
    # 4. No click: the episode's own next attempt must recover.
    samples, recovered = await m.until(lambda s: s["health"] == "live", timeout=10.0)
    await asyncio.sleep(0.3)                    # let the reset attributes land before reading them
    end = await m.state()
    w = await m.wrapper()
    attempts = w["socks"][n0:]
    starts = [a["created"] for a in attempts]
    gaps = [round((b - a) / 1000, 3) for a, b in itertools.pairwise(starts)]
    refused_now = (await m.status())["api"]["refused_clients"]
    not_opened = [i + 1 for i, a in enumerate(attempts) if a["opened"] is None]
    recovering = next((i + 1 for i, a in enumerate(attempts) if a["opened"] is not None), None)
    changes = await m.attrs(imm["now"])
    seen = [imm["attempts"]] + [int(x["v"]) for x in changes if x["a"] == "attempts"]
    steps = [b - a for a, b in itertools.pairwise(seen)]
    # The refused attempt ended when its socket closed; the next started one episode wait later (2 s).
    wait_after_refusal = (round((attempts[1]["created"] - attempts[0]["closed"]) / 1000, 3)
                          if len(attempts) >= 2 and attempts[0]["closed"] else None)
    await m.shot("m3b-a6-slots-c-recovered.png")
    # 5. A fresh fault: a new episode whose first attempt is number 1.
    t_second = (await m.state())["now"]
    await m.cdp.js("window.__m3b.bad()")
    second, recovered2 = await m.until(lambda s: s["health"] == "live", timeout=10.0)
    second_changes = await m.attrs(t_second)
    second_attempts = [int(x["v"]) for x in second_changes if x["a"] == "attempts"]
    second_episode = [x["v"] for x in second_changes if x["a"] == "episode"]
    allsamples = samples + second
    m.cases.record(
        "Recovery with all four client slots occupied, variant C (deterministic 503, automatic recovery)",
        "The script holds the freed slot, waits until refused_clients rises (a real 503 to the page's attempt), then "
        "releases it. The page's next scheduled attempt recovers without Retry now; health live; data-episode none "
        "and data-attempts 0 (the budget reset); data-timers never above 1; no two attempts less than 0.9 s apart; "
        "refused_clients rose by exactly the refused attempts. A second malformed frame then starts a fresh episode "
        "at attempt 1",
        {"setup: the script held the slot before the page's first attempt": setup["clients_now"] == 4
         and setup["page_sockets_open"] == 0,
         "the refusal was observed (refused_clients rose) before the release": refused_series[-1] > refused0,
         "the first attempt was refused, a later one recovered": bool(not_opened) and not_opened[0] == 1
         and recovering is not None and recovering > 1,
         "recovered without Retry now, within the episode's waits": recovered and recovering is not None
         and recovering <= 3 and wait_after_refusal is not None and abs(wait_after_refusal - 2.0) <= 0.3,
         "after: live, data-episode none, data-attempts 0": (end["health"], end["episode"], end["attempts"])
         == ("live", "none", 0),
         "data-timers never above 1": all(s["timers"] <= 1 for s in allsamples)
         and all(int(x["v"]) <= 1 for x in changes + second_changes if x["a"] == "timers"),
         "no two attempts less than 0.9 s apart": all(g >= 0.9 for g in gaps),
         "each attempt raises data-attempts by exactly 1": all(x == 1 for x in steps if x > 0)
         and sum(1 for x in steps if x > 0) == len(attempts),
         "refused_clients rose by exactly the refused attempts": refused_now - refused0 == len(not_opened),
         "a second fault starts a fresh episode at attempt 1, and recovers": recovered2
         and next((v for v in second_attempts if v), None) == 1 and "active" in second_episode
         and second[-1]["episode"] == "none" and second[-1]["attempts"] == 0},
        {"setup": setup, "refused_series": refused_series, "refused_before": refused0, "refused_after": refused_now,
         "t_bad_ms": imm["now"], "t_refusal_seen_ms": t_refused, "t_released_ms": t_released,
         "attempt_starts_ms": starts, "start_gaps_s": gaps, "refused_attempts": not_opened,
         "recovering_attempt": recovering, "wait_after_refusal_s": wait_after_refusal,
         "live_after_bad_s": (samples[-1]["now"] - imm["now"]) / 1000, "attempts_seen": seen,
         "end": slim(end), "attempt_sockets": attempts, "attribute_changes": changes,
         "second_fault": {"attempts_seen": second_attempts, "episode_seen": second_episode,
                          "live_after_s": (second[-1]["now"] - t_second) / 1000, "end": slim(second[-1])}})


async def run_slot_cases(run: Run, m: M3b) -> None:
    """The three four-client variants, with the capture script holding the other three slots."""
    clients = ScriptClients(m.http)
    try:
        for _ in range(3):
            await clients.open()
        await asyncio.sleep(0.5)
        run.log(f"script clients held: {len(clients.held)}; /status clients "
                f"{(await m.status())['api']['clients']}")
        await case_slots(m, clients, "A")
        await asyncio.sleep(3.0)
        await case_slots(m, clients, "B")
        await asyncio.sleep(3.0)
        await case_slots_c(m, clients)
    finally:
        await clients.close_all()


async def part_a(run: Run, m: M3b) -> None:
    sim = await m3b_start(run, DEFAULT_PROFILE, "a-simulator.log")
    try:
        await case_no_false_invalidation(m)
        await case_malformed_once(m)
        await case_malformed_bounded(m)
        await run_slot_cases(run, m)
    finally:
        run.stop(sim)


# ---- part B: the real simulator, the stepped demo, with traffic ----

async def overflow_at(m: M3b, label: str, size: tuple[int, int]) -> None:
    await m.cdp.viewport(*size)
    await asyncio.sleep(1.2)
    if size == NARROW:
        await m.cdp.js("document.getElementById('graphs-panel').scrollIntoView({block: 'start'})")
        await asyncio.sleep(0.5)
    await check_overflow(m.run, m.cdp, f"M3b {label}")
    measured = await m.cdp.js(OVERFLOW)
    m.cases.extra.setdefault("overflow", {})[label] = measured
    m.cases.extra.setdefault("status_bar_px", {})[label] = await m.cdp.js(
        "Math.round(document.querySelector('header').getBoundingClientRect().height)")
    await m.shot(f"m3b-b-graphs-{label}.png")
    if size == NARROW:
        await m.cdp.js("window.scrollTo(0, 0)")


async def case_window_persists(m: M3b) -> None:
    await m.click_window(30)
    stored = await m.cdp.js("localStorage.getItem('ecu-simulator.graphs.window')")
    await m.cdp.send("Page.reload")
    await m.cdp.wait_for("document.body && document.body.dataset.health === 'live'", timeout=20)
    # The cards exist and have written their window (whatever it is): then it is read.
    await m.cdp.wait_for("document.querySelectorAll('#graphs-grid figure.graph[data-window-s]').length === 5", 10)
    s = await m.state()
    pressed = await m.cdp.js("[...document.querySelectorAll('#graphs-panel [data-window]')]"
                             ".map(b => [b.dataset.window, b.getAttribute('aria-pressed')])")
    wins = [g["windowS"] for g in s["graphs"].values()]
    m.cases.record(
        "Window persists", "Click 30 s, reload: data-window-s = 30 everywhere; 30 s has aria-pressed=true",
        {"data-window-s 30 everywhere": bool(wins) and all(x == "30" for x in wins),
         "30 s aria-pressed true, the others false": sorted(pressed) == [["120", "false"], ["30", "true"],
                                                                        ["600", "false"]]},
        {"stored": stored, "window_s": wins, "aria_pressed": pressed})


async def case_held_left(m: M3b) -> None:
    await m.until_as_of(55.0)
    s = await m.state()
    sp = s["graphs"]["vehicle.speed"]
    await m.shot("m3b-b-held-left-30s.png")
    m.cases.record(
        "Held value at the left edge of a trimmed window",
        "Stepped demo, 30 s window, read at t ~ 55 s (speed 80 since t = 21): data-left-value = 80; line 2 reads "
        "min 80 · max 80 in 30 s; data-segments = 1; data-oldest-t < data-as-of - 30",
        {"data-left-value 80": sp["leftValue"] == "80",
         "line 2 min 80 · max 80 in 30 s": sp["line2"].startswith("min 80 · max 80 in 30 s"),
         "data-segments 1": sp["segments"] == "1",
         "data-oldest-t < data-as-of - 30": float(sp["oldestT"]) < float(sp["asOf"]) - 30},
        {"speed": sp})


async def case_pause(m: M3b) -> None:
    before_log = await m.log()
    await m.cdp.click("#btn-graphs-pause")
    await asyncio.sleep(0.3)
    samples = []
    for _ in range(21):                       # 10 s, every 0.5 s
        samples.append(await m.state())
        await asyncio.sleep(0.5)
    during_log = await m.log()
    meta = await m.cdp.js("document.getElementById('graphs-meta').textContent")
    await m.shot("m3b-b-paused.png")
    await m.cdp.click("#btn-graphs-pause")
    await asyncio.sleep(0.8)
    after = await m.state()
    paths = [p for p, g in samples[0]["graphs"].items() if not g["hidden"]]

    def seq(key: str, p: str) -> list[str]:
        return [s["graphs"][p][key] for s in samples]

    m.cases.record(
        "Pause while buffering",
        "Pause graphs for 10 s, then resume. While paused: data-paused = true; data-drawn-to and line 1 unchanged; "
        "data-as-of rising; data-points not falling except by the caps. After: data-drawn-to = data-as-of. The log "
        "kept running",
        {"graphs shown": bool(paths),
         "paused true throughout": all(s["graphs"][p]["paused"] == "true" for s in samples for p in paths),
         "drawn-to unchanged": all(len(set(seq("drawnTo", p))) == 1 for p in paths),
         "line 1 unchanged": all(len(set(seq("line1", p))) == 1 for p in paths),
         "as-of rising": all(float(seq("asOf", p)[-1]) > float(seq("asOf", p)[0]) for p in paths),
         "points not falling": all(all(int(b) >= int(a) for a, b in itertools.pairwise(seq("points", p)))
                                   for p in paths),
         "after: drawn-to = as-of, paused false": all(after["graphs"][p]["drawnTo"] == after["graphs"][p]["asOf"]
                                                      and after["graphs"][p]["paused"] == "false" for p in paths),
         "the log kept running": len(during_log["seqs"]) > len(before_log["seqs"])
         or (during_log["seqs"][-1:] != before_log["seqs"][-1:])},
        {"meta": meta, "first": {p: samples[0]["graphs"][p] for p in paths},
         "last": {p: samples[-1]["graphs"][p] for p in paths}, "after": {p: after["graphs"][p] for p in paths},
         "log_last_seq": [before_log["seqs"][-1:], during_log["seqs"][-1:]]})


async def case_sigstop(m: M3b, sim: subprocess.Popen[bytes]) -> None:
    await asyncio.sleep(1.0)
    st = await m.status()
    before = await m.state()
    os.kill(sim.pid, signal.SIGSTOP)
    m.run.log(f"SIGSTOP the simulator (pid {sim.pid})")
    t0 = time.time() * 1000
    samples, down = await m.until(lambda s: s["conn"] == "down", timeout=12.0)
    d = samples[-1]
    await asyncio.sleep(0.3)
    d2 = await m.state()
    await m.shot("m3b-b-sigstop-stale.png")
    m.cases.record(
        "Disconnect without restart (SIGSTOP)",
        "Within 8 s: conn text starts Disconnected; data-conn down, data-health stale, data-episode none; the "
        "banner is shown; the panels carry Stale, as of",
        {"down within 8 s": down and (d["now"] - t0) / 1000 <= 8.0,
         "conn text starts Disconnected": d2["text"].startswith("Disconnected"),
         "down, stale, episode none": (d2["conn"], d2["health"], d2["episode"]) == ("down", "stale", "none"),
         "banner shown": bool(d2["banner"]),
         "panels carry Stale, as of": len(d2["stale"]) >= 3 and all(t.startswith("Stale, as of") for t in d2["stale"])},
        {"down_after_s": (d["now"] - t0) / 1000, "state": slim(d2)})
    os.kill(sim.pid, signal.SIGCONT)
    m.run.log("SIGCONT the simulator")
    t1 = time.time() * 1000
    samples, live = await m.until(lambda s: s["health"] == "live", timeout=20.0, period=0.2)
    t_live = samples[-1]["now"]
    paths = [p for p, g in before["graphs"].items() if not g["hidden"]]
    # Wait for the break to be taken (the first state with a later as_of on the new socket), then
    # for the log's own marker (it re-renders at most every 200 ms); the rules are checked after.
    taken, _ = await m.until(lambda s: bool(s["breaks"]) and all(
        int(s["graphs"][p]["gaps"]) > int(before["graphs"][p]["gaps"]) for p in paths), timeout=6.0)
    with contextlib.suppress(RuntimeError):
        await m.cdp.wait_for("document.getElementById('log-body').innerText.indexOf("
                             "'Connection lost, then resumed.') >= 0", timeout=3.0)
    after = await m.state()
    st2 = await m.status()
    lg = await m.log()
    note = re.search(GAP_NOTE, after["breaks"])          # the shared gap line under the graphs head
    await m.shot("m3b-b-sigcont-break.png")
    # The extra log-rows case again, with a disconnect break showing and no encode-failed readout.
    await m.cdp.js("window.scrollTo(0, 0)")
    rows = await m.cdp.js(LOG_ROWS)
    m.cases.record(
        "Log rows at 1440 x 900, graphs open, a break note showing (extra, disconnect break)",
        "At least 5 full log rows inside #logwrap at 1440 x 900 with the graphs open and a break note showing",
        {"a break note is shown": re.search(GAP_NOTE, rows["breaks"]) is not None,
         "at least 5 full log rows": rows["full"] >= 5}, rows)
    m.cases.record(
        "... then SIGCONT",
        "SIGCONT, Live within the backoff (<= 16 s): GET /status started_at equals the value read before; no "
        "Simulator restarted. marker, and Connection lost, then resumed.; data-run unchanged; data-gaps rose by 1 and "
        "data-segments by 1; line 2 names the gap's scenario times; seq continuity",
        {"live within 16 s": live and (t_live - t1) / 1000 <= 16.0,
         "started_at equal": st["started_at"] == st2["started_at"],
         "no restart marker": "Simulator restarted" not in lg["text"],
         "Connection lost, then resumed.": "Connection lost, then resumed." in lg["text"],
         "graphs shown": bool(paths),
         "data-run unchanged": all(after["graphs"][p]["run"] == before["graphs"][p]["run"] for p in paths),
         "gaps +1 and segments +1": all(int(after["graphs"][p]["gaps"]) == int(before["graphs"][p]["gaps"]) + 1
                                        and int(after["graphs"][p]["segments"]) ==
                                        int(before["graphs"][p]["segments"]) + 1 for p in paths),
         "the shared gap line names the gap's scenario times (disconnected)": note is not None
         and note.group(1) == "disconnected",
         "line 2 is min and max only": all(re.fullmatch(r"(min .* · max .*|no valid value) in .*",
                                                        after["graphs"][p]["line2"]) for p in paths),
         "seq continuity": seq_continuous(lg["seqs"]) and lg["gaps"] == 0 and "duplicate" not in lg["count"]},
        {"live_after_s": (t_live - t1) / 1000, "started_at": [st["started_at"], st2["started_at"]],
         "before": {p: {k: before["graphs"][p][k] for k in ("gaps", "segments", "run")} for p in paths},
         "after": {p: {k: after["graphs"][p][k] for k in ("gaps", "segments", "run", "line2")} for p in paths},
         "breaks_line": after["breaks"],
         "seqs": [len(lg["seqs"]), lg["seqs"][:3], lg["seqs"][-3:]], "log_count": lg["count"]})


async def case_restart_and_gap(m: M3b, sim: subprocess.Popen[bytes]) -> None:
    """Measured (Task 36): at 1440 x 900, the restart note and a gap note shown together, count the
    full log rows. Just after the restart (the note shows while the window reaches the new run's
    start), a SIGSTOP/SIGCONT of the new simulator adds a disconnect gap inside the window. Traffic
    runs, and the count is taken once the newest rows are exchanges, not the short marker rows
    (restart, connection lost) that would otherwise fill the bottom of the log."""
    traffic = start_traffic(m.run)
    try:
        await _restart_and_gap(m, sim)
    finally:
        m.run.stop(traffic)


async def _restart_and_gap(m: M3b, sim: subprocess.Popen[bytes]) -> None:
    await m.cdp.viewport(*WIDE)
    await m.until_as_of(3.0)
    os.kill(sim.pid, signal.SIGSTOP)
    m.run.log(f"SIGSTOP the restarted simulator (pid {sim.pid}) for a gap beside the restart note")
    await m.until(lambda s: s["conn"] == "down", timeout=12.0)
    os.kill(sim.pid, signal.SIGCONT)
    m.run.log("SIGCONT the restarted simulator")
    await m.until(lambda s: s["health"] == "live", timeout=20.0, period=0.2)
    await m.until(lambda s: re.search(GAP_NOTE, s["breaks"]) is not None, timeout=6.0)
    await m.cdp.wait_for("[...document.querySelectorAll('#log-body > tr')].slice(-6)"
                         ".every(r => r.querySelector('td.c-seq'))", timeout=15)
    await m.cdp.js("window.scrollTo(0, 0)")
    await m.settled()
    # Three readings 1 s apart, the screenshot straight after the last; the case uses the fewest
    # rows (the worst moment), and every reading is recorded.
    readings = []
    for k in range(3):
        if k:
            await asyncio.sleep(1.0)       # spacing between readings; no page condition to wait on
        await m.settled()
        readings.append(await m.cdp.js(LOG_ROWS))
    await m.shot("m3b-b-restart-and-gap-1440.png")
    readings.append(await m.cdp.js(LOG_ROWS))
    rows = dict(min(readings, key=lambda r: (r["full"], r["rowsRegion"])))
    rows["readings"] = readings
    m.cases.record(
        "Log rows at 1440 x 900, restart note and a gap note together (measured)",
        "At 1440 x 900 with the graphs open, the restart note and a gap note visible together: at least 5 full "
        "log rows inside #logwrap",
        # Since Task 37 the restart note is on the shared notice line, first, " · "-joined with the gap.
        {"the restart note is shown, complete": rows["breaks"].startswith("Simulator restarted at")
         and "the previous run's graphs were cleared." in rows["breaks"],
         "a gap note is shown": re.search(GAP_NOTE, rows["breaks"]) is not None,
         "the newest rows are exchange rows": rows["fullExchange"] == rows["full"],
         "the log is filled (more rows than fit)": len((await m.log())["seqs"]) > rows["full"],
         "at least 5 full log rows": rows["full"] >= 5}, rows)


async def case_restart(m: M3b, run: Run, sim: subprocess.Popen[bytes], logname: str) -> subprocess.Popen[bytes]:
    before = await m.state()
    run.log("SIGTERM the simulator, then start a new one")
    run.stop(sim)
    await m.until(lambda s: s["conn"] != "live", timeout=12.0)
    sim = await m3b_start(run, MOVING_PROFILE, logname)
    samples, live = await m.until(lambda s: s["health"] == "live", timeout=30.0, period=0.2)
    paths = [p for p, g in before["graphs"].items() if not g["hidden"]]

    # The first new point is stored, and a later as_of has drawn it (one point plus the right edge).
    def first_point(s: dict[str, Any]) -> bool:
        return all(s["graphs"][p]["run"] != before["graphs"][p]["run"] and int(s["graphs"][p]["points"] or 0) >= 1
                   and float(s["graphs"][p]["drawnTo"] or 0) > float(s["graphs"][p]["oldestT"] or 0) for p in paths)

    samples2, got = await m.until(first_point, timeout=10.0)
    s = samples2[-1]
    lg = await m.log()
    await m.shot("m3b-b-restart.png")
    m.cases.record(
        "Restart reset",
        "SIGTERM, then a new simulator: data-run changed; data-oldest-t >= 0 and data-points counts only the new run; "
        "the restart note and marker are visible; data-segments = 1 after the first new point",
        {"graphs shown": bool(paths), "live again": live, "the first new point drawn": got,
         "data-run changed": all(s["graphs"][p]["run"] != before["graphs"][p]["run"] for p in paths),
         "oldest-t >= 0 and only this run's points": all(
             0 <= float(s["graphs"][p]["oldestT"]) <= float(s["graphs"][p]["asOf"])
             and int(s["graphs"][p]["points"]) <= 4 * float(s["graphs"][p]["asOf"]) + 4 for p in paths),
         # Since Task 37 the restart note is on the shared notice line, not in #graphs-note.
         "restart note visible": s["breaks"].startswith("Simulator restarted at"),
         "restart marker in the log": "Simulator restarted." in lg["text"],
         "segments 1 after the first new point": all(s["graphs"][p]["segments"] == "1" for p in paths)},
        {"before": {p: {k: before["graphs"][p][k] for k in ("run", "points", "oldestT", "asOf")} for p in paths},
         "after": {p: {k: s["graphs"][p][k] for k in ("run", "points", "oldestT", "newestT", "asOf", "drawnTo",
                                                     "segments")}
                   for p in paths}, "notice_line": s["breaks"]})
    return sim


def trace_summary(path: Path, seconds: float) -> dict[str, Any]:
    """Main-thread busy time and the graphs' redraw time, from a devtools.timeline + v8 trace."""
    import gzip

    with gzip.open(path, "rt") as fh:
        data = json.load(fh)
    events = data["traceEvents"] if isinstance(data, dict) else data
    mains = {(e["pid"], e["tid"]) for e in events
             if e.get("ph") == "M" and e.get("name") == "thread_name"
             and e.get("args", {}).get("name") == "CrRendererMain"}
    src = (ROOT / "src" / "ecu_simulator" / "api" / "static" / "app.js").read_text().splitlines()
    draw_line = next(i for i, line in enumerate(src) if "G.frame = requestAnimationFrame" in line)
    resize_line = next(i for i, line in enumerate(src) if "G.resizeFrame = requestAnimationFrame" in line)

    def app_calls(thread: tuple[int, int], line0: int) -> list[float]:
        return [e["dur"] / 1000 for e in events
                if (e.get("pid"), e.get("tid")) == thread and e.get("name") == "FunctionCall" and "dur" in e
                and str(e.get("args", {}).get("data", {}).get("url", "")).endswith("/app.js")
                and e["args"]["data"].get("lineNumber") in (line0, line0 + 1)]

    best: dict[str, Any] = {}
    for thread in mains:
        xs = sorted((e["ts"], e["ts"] + e["dur"]) for e in events
                    if (e.get("pid"), e.get("tid")) == thread and e.get("ph") == "X" and e.get("dur"))
        busy, cur_s, cur_e = 0.0, None, None
        for s, e in xs:
            if cur_e is None or s > cur_e:
                if cur_e is not None and cur_s is not None:
                    busy += cur_e - cur_s
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        if cur_e is not None and cur_s is not None:
            busy += cur_e - cur_s
        draws = app_calls(thread, draw_line)
        summary = {
            "thread": list(thread), "events": len(xs), "main_thread_busy_ms": round(busy / 1000, 1),
            "busy_percent_of_window": round(busy / 1000 / (seconds * 1000) * 100, 2),
            "graph_redraws": len(draws), "graph_redraw_ms_total": round(sum(draws), 1),
            "graph_redraw_ms_max": round(max(draws), 2) if draws else None,
            "graph_redraw_ms_median": round(sorted(draws)[len(draws) // 2], 3) if draws else None,
            "resize_ms_total": round(sum(app_calls(thread, resize_line)), 1),
            "fire_animation_frame_ms_total": round(sum(
                e["dur"] for e in events if (e.get("pid"), e.get("tid")) == thread
                and e.get("name") == "FireAnimationFrame" and "dur" in e) / 1000, 1),
            "function_call_ms_total": round(sum(e["dur"] for e in events if (e.get("pid"), e.get("tid")) == thread
                                                and e.get("name") == "FunctionCall" and "dur" in e) / 1000, 1),
            "app_js_lines_matched": [draw_line + 1, resize_line + 1],
        }
        if not best or summary["graph_redraws"] > best["graph_redraws"]:
            best = summary
    return best


async def measure_cost(m: M3b, label: str, size: tuple[int, int], seconds: float = 60.0) -> None:
    """§11.3: heap at the start, every 10 s and at the end; a DevTools trace; TaskDuration; canvas sizes."""
    cdp = m.cdp
    await cdp.viewport(*size)
    await asyncio.sleep(1.5)
    if size == NARROW:
        await cdp.js("document.getElementById('graphs-panel').scrollIntoView({block: 'start'})")
    await asyncio.sleep(1.5)
    await cdp.send("Performance.enable")

    async def metrics() -> dict[str, float]:
        res = await cdp.send("Performance.getMetrics")
        return {x["name"]: x["value"] for x in res["metrics"]}

    complete: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    def traced(params: dict[str, Any]) -> None:
        if not complete.done():
            complete.set_result(params)

    cdp.handlers["Tracing.tracingComplete"] = traced
    await cdp.send("Tracing.start", transferMode="ReturnAsStream", streamCompression="gzip",
                   traceConfig={"includedCategories": ["devtools.timeline", "v8"]})
    m0 = await metrics()
    heap = [await cdp.js(HEAP)]
    t0 = time.monotonic()
    for k in range(1, int(seconds // 10) + 1):
        await asyncio.sleep(max(0.0, t0 + 10 * k - time.monotonic()))
        heap.append(await cdp.js(HEAP))
    m1 = await metrics()
    elapsed = time.monotonic() - t0
    canv = await cdp.js(CANVASES)
    s = await m.state()
    await cdp.send("Tracing.end")
    done = await asyncio.wait_for(complete, 60)
    trace_path = m.run.outdir / f"m3b-trace-{label}.json.gz"
    with trace_path.open("wb") as fh:
        while True:
            chunk = await cdp.send("IO.read", handle=done["stream"], size=1 << 20)
            raw = chunk.get("data", "")
            fh.write(base64.b64decode(raw) if chunk.get("base64Encoded") else raw.encode())
            if chunk.get("eof"):
                break
    await cdp.send("IO.close", handle=done["stream"])
    await cdp.send("Performance.disable")
    cdp.handlers.pop("Tracing.tracingComplete", None)
    bytes_ = sum(c["w"] * c["h"] * 4 for c in canv["canvases"])
    result = {
        "label": label, "viewport": list(size), "seconds": round(elapsed, 2),
        "heap": heap, "heap_used_mib": [round(h["used"] / 2**20, 2) for h in heap],
        "heap_total_mib": [round(h["total"] / 2**20, 2) for h in heap],
        "TaskDuration_s": round(m1["TaskDuration"] - m0["TaskDuration"], 3),
        "ScriptDuration_s": round(m1["ScriptDuration"] - m0["ScriptDuration"], 3),
        "LayoutDuration_s": round(m1["LayoutDuration"] - m0["LayoutDuration"], 3),
        "RecalcStyleDuration_s": round(m1["RecalcStyleDuration"] - m0["RecalcStyleDuration"], 3),
        "TaskDuration_percent": round((m1["TaskDuration"] - m0["TaskDuration"]) / elapsed * 100, 2),
        "canvases": canv, "canvas_bytes": bytes_, "canvas_mib": round(bytes_ / 2**20, 3),
        "points": {p: g["points"] for p, g in s["graphs"].items()}, "health": s["health"],
        "trace_file": trace_path.name, "trace_bytes": trace_path.stat().st_size,
        "trace": trace_summary(trace_path, elapsed),
    }
    m.cases.cost.append(result)
    m.run.log(f"cost {label}: heap used MiB {result['heap_used_mib']}, TaskDuration {result['TaskDuration_s']} s "
              f"over {result['seconds']} s, canvases {result['canvas_mib']} MiB, trace {result['trace']}")
    await m.shot(f"m3b-b-cost-{label}.png")
    if size == NARROW:
        await cdp.js("window.scrollTo(0, 0)")


async def part_b(run: Run, m: M3b) -> None:
    sim = await m3b_start(run, MOVING_PROFILE, "b-simulator-1.log")
    traffic = None
    try:
        await m.open_page()
        await case_window_persists(m)
        traffic = start_traffic(run)
        await m.until_as_of(24.0)
        await overflow_at(m, "1440", WIDE)
        rows = await m.cdp.js(LOG_ROWS)
        m.cases.record("Log rows at 1440 x 900", "Section open, log filled: at least 5 full log rows inside #logwrap",
                       {"graphs open": (await m.cdp.js("!document.getElementById('graphs').hidden")) is True,
                        "log filled (more rows than fit)": (await m.log())["seqs"].__len__() > rows["full"],
                        "at least 5 full log rows": rows["full"] >= 5}, rows)
        await overflow_at(m, "1200", MID)
        await overflow_at(m, "390", NARROW)
        await overflow_at(m, "2000", OWNER_WIDE)
        await m.cdp.viewport(*WIDE)
        await asyncio.sleep(1.0)
        over = m.cases.extra["overflow"]

        def fits(measured: list[list[Any]]) -> bool:
            names = [n for n, _, _ in measured]
            return (all(sw <= cw for _, sw, cw in measured) and "#graphs" in names
                    and sum(n.startswith(".uplot") for n in names) == 5)

        m.cases.record("Overflow", "#graphs and each .uplot in OVERFLOW, at 1440, 390 and 2000 (and 1200): no "
                       "scrollWidth above its clientWidth",
                       {f"{label}: none over, #graphs and 5 .uplot measured": fits(v) for label, v in over.items()},
                       {"measured": over, "status_bar_px": m.cases.extra["status_bar_px"]})
        await case_held_left(m)
        await case_pause(m)
        await m.click_window(120)
        await measure_cost(m, "1440x900", WIDE)
        await measure_cost(m, "390x844", NARROW)
        await m.cdp.viewport(*WIDE)
        await asyncio.sleep(1.0)
        await m.shot("m3b-b-live-1440.png")
        run.log("stop traffic")
        run.stop(traffic)
        traffic = None
        await case_sigstop(m, sim)
        sim = await case_restart(m, run, sim, "b-simulator-2.log")
        await case_restart_and_gap(m, sim)
    finally:
        if sim.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(sim.pid, signal.SIGCONT)
        run.stop(traffic)
        run.stop(sim)


# ---- parts C to E: the fault-injection test server (§12.3) ----

async def part_c_nonfinite(run: Run, m: M3b) -> None:
    """ice_scenario.yaml, engine.coolant_temp nan for scenario t in [10, 20). No traffic."""
    path = "engine.coolant_temp"
    sim = await m3b_start(run, SCENARIO_PROFILE, "c-fault-server.log", ["--nonfinite", f"{path}:10:20"])
    try:
        await m.open_page()
        await m.until_as_of(8.0)
        samples = []
        shot_taken = False
        while True:
            s = await m.state()
            s["cell"] = s["cells"].get(path)               # read in the same evaluation as the graph
            samples.append(s)
            t = float(s["graphs"][path]["asOf"])
            if s["graphs"][path]["state"] == "invalid" and t > 14 and not shot_taken:
                await m.shot("m3b-c-nonfinite-invalid.png")
                shot_taken = True
            if t > 24:
                break
            await asyncio.sleep(0.1)
        await m.shot("m3b-c-nonfinite-after.png")
        g = [(float(s["graphs"][path]["asOf"]), s) for s in samples]
        invalid = [s for t, s in g if s["graphs"][path]["state"] == "invalid"]
        valid_after = [s for t, s in g if t >= 20 and s["graphs"][path]["state"] == "ok"]
        last = samples[-1]["graphs"][path]
        inv_newest = {s["graphs"][path]["newestT"] for s in invalid}
        inv_points = {s["graphs"][path]["points"] for s in invalid}
        first_valid = valid_after[0]["graphs"][path] if valid_after else None
        m.cases.record(
            "Non-finite values",
            "Fault server, scenario profile, engine.coolant_temp nan for t in [10, 20) in-process, no traffic. While "
            "invalid: data-state invalid; line 1 and the table read invalid value; conn text Live; "
            "data-malformed-total 0. After: data-gaps >= 1, data-segments >= 2, and no point is drawn between the "
            "last valid and the next valid t",
            {"invalid seen, only inside [10, 20)": bool(invalid) and all(10 <= float(s["graphs"][path]["asOf"]) < 20
                                                                       for s in invalid),
             "while invalid: line 1 and the table read invalid value": all(
                 s["graphs"][path]["line1"] == "invalid value" and s["cell"] == "invalid value" for s in invalid),
             "while invalid: conn text Live, malformed 0": all(s["text"] == "Live" and s["malformed"] == 0
                                                               for s in invalid),
             "after: gaps >= 1, segments >= 2": int(last["gaps"]) >= 1 and int(last["segments"]) >= 2,
             # The ring's newest point is the NaN point for the whole invalid stretch, and the first valid
             # point after it (t >= 20) is the next one stored: nothing in between.
             "no point between the last valid and the next valid t": len(inv_newest) == 1 and len(inv_points) == 1
             and first_valid is not None and float(first_valid["newestT"]) >= 20
             and int(first_valid["points"]) == int(next(iter(inv_points))) + 1},
            {"invalid_samples": len(invalid),
             "invalid_as_of": [invalid[0]["graphs"][path]["asOf"], invalid[-1]["graphs"][path]["asOf"]]
             if invalid else None,
             "invalid_newest_t": sorted(inv_newest), "invalid_points": sorted(inv_points),
             "first_valid_after": first_valid, "last": last,
             "breaks_line_while_invalid": invalid[-1]["breaks"] if invalid else None,
             "breaks_line_after": samples[-1]["breaks"],
             "invalid_example": slim(invalid[0], (path,)) | {"cell": invalid[0]["cell"]} if invalid else None})
    finally:
        run.stop(sim)


async def part_d_encoding(run: Run, m: M3b) -> None:
    """The stepped demo, the full snapshot raising for scenario t in [30, 35), with traffic."""
    sim = await m3b_start(run, MOVING_PROFILE, "d-fault-server.log", ["--state-fault", "30:35"])
    traffic = None
    try:
        origin = await scenario_origin()          # wall s at scenario t = 0; the fault opens at origin + 30
        run.log(f"scenario origin estimated at wall {origin:.3f}")
        await m.open_page()
        traffic = start_traffic(run)
        await m.until_as_of(28.0)
        before = await m.state()
        n0 = before["socks"]
        await m.until_as_of(29.6)
        samples: list[dict[str, Any]] = []
        while True:
            s = await m.state()
            samples.append(s)
            if s["now"] - samples[0]["now"] > 25000 or (s["health"] == "live" and s["now"] - samples[0]["now"] > 8000):
                break
            await asyncio.sleep(0.1)
        paths = [p for p, g in before["graphs"].items() if not g["hidden"]]
        # The break is taken by the first state with a later as_of: wait for it (it is checked below).
        await m.until(lambda s: all(int(s["graphs"][p]["gaps"]) > int(before["graphs"][p]["gaps"]) for p in paths),
                      timeout=5.0)
        after = await m.state()
        w = await m.wrapper()
        fault = [s for s in samples if s["reason"] == "encoding"]
        first_fault = fault[0] if fault else None
        # The page's as_of stops below 30 while no state is pushed, so the opening is taken from the clock.
        t_open = (origin + 30.0) * 1000
        live_again = next((s for s in samples if s is not samples[0] and fault and s["now"] > fault[-1]["now"]
                           and s["health"] == "live"), None)
        statuses = w["status"]
        false_reads = [r for r in statuses if r["ok"] is False]
        # The rule itself, from the wrapper's logs: t_live is the first health -> live change after
        # the last fault sample; no health -> live change may come between the fault's start and it.
        health = [x for x in await m.attrs(samples[0]["now"]) if x["a"] == "health"]
        t_fault = next((x["t"] for x in health if x["v"] != "live"), None)
        t_live = next((x["t"] for x in health if fault and x["v"] == "live" and x["t"] > fault[-1]["now"]), None)
        early_live = [x for x in health if x["v"] == "live" and t_fault is not None and t_live is not None
                      and t_fault < x["t"] < t_live]
        # The socket whose state made it live: the newest socket opened before t_live.
        idx = max((i for i, x in enumerate(w["socks"]) if i >= n0 and t_live is not None and x["created"] <= t_live),
                  default=None)
        recovered_sock = w["socks"][idx] if idx is not None else None
        first_state = next((x["t"] for x in w["states"] if x["sock"] == idx), None) if idx is not None else None
        qualifying_ok = None
        if recovered_sock and false_reads:
            qualifying_ok = next((r for r in reversed(statuses) if r["ok"] is True and r["end"] is not None
                                  and false_reads[-1]["end"] < r["end"] <= recovered_sock["created"]), None)
        await m.shot("m3b-d-encoding-recovered.png")
        fault_polls = [s["polls"] for s in fault]
        # The gaps, in the spec's meaning (§6.7, §8.4; final review): the fault breaks the graphs, and
        # the resync that follows the ok:true poll breaks them again. Both fall into one gap when no
        # state reached the page between them; when the simulator's first good state came on the
        # fault-time socket (§8.3) before the resync closed it, that state ends the first gap and the
        # resync's own gap follows: two gaps. So: +1, or +2 when a state arrived on that socket after
        # the fault. (Before the final review this expected +1 always, and the resync was drawn held.)
        fault_sock = n0 - 1
        late_on_fault_sock = [x for x in w["states"] if x["sock"] == fault_sock and t_fault is not None
                              and x["t"] > t_fault]
        expected_gaps = 1 + (1 if late_on_fault_sock else 0)
        m.cases.record(
            "Encoding failure, then recovery to changed data",
            "Fault server, stepped scenario, the full snapshot raises for 5 s. Within one poll (<= 3 s): data-data "
            "last-known, data-reason encoding, the banner names state_encode_failed; data-conn stays live and "
            "data-polls keeps rising. The graphs get a break. After the fault ends: live again, and only after a state "
            "was applied on a socket established after a /status with ok: true",
            {"last-known/encoding within 3 s of the fault": first_fault is not None
             and (first_fault["now"] - t_open) / 1000 <= 3.0 and first_fault["data"] == "last-known",
             "the banner names state_encode_failed": bool(fault) and all("state_encode_failed" in (s["banner"] or "")
                                                                         for s in fault),
             "data-conn stays live during the fault": bool(fault) and all(s["conn"] == "live" for s in fault),
             "data-polls keeps rising": len(set(fault_polls)) >= 2 and fault_polls == sorted(fault_polls),
             "the graphs get a break (and the resync its own, when the fault's gap had already ended)": all(
                 int(after["graphs"][p]["gaps"]) == int(before["graphs"][p]["gaps"]) + expected_gaps
                 and int(after["graphs"][p]["segments"]) == int(before["graphs"][p]["segments"]) + expected_gaps
                 for p in paths),
             "graphs shown": bool(paths),
             "live again after the fault": live_again is not None and t_live is not None,
             "no health -> live change between the fault and t_live": t_fault is not None and not early_live,
             "the recovering socket was opened after an ok:true /status that followed the last ok:false":
             recovered_sock is not None and qualifying_ok is not None,
             "its first state arrived no later than t_live": first_state is not None and t_live is not None
             and first_state <= t_live},
            {"first_fault_after_s": (first_fault["now"] - t_open) / 1000 if first_fault else None,
             "fault_samples": len(fault), "fault_polls": [fault_polls[:1], fault_polls[-1:]],
             "banner": fault[0]["banner"] if fault else None,
             "live_again_after_s": (live_again["now"] - t_open) / 1000 if live_again else None,
             "t_fault_ms": t_fault, "t_live_ms": t_live, "first_state_ms": first_state,
             "ok_true_end_ms": qualifying_ok["end"] if qualifying_ok else None,
             "last_ok_false_end_ms": false_reads[-1]["end"] if false_reads else None,
             "recovering_socket": {"index": idx, **(recovered_sock or {})}, "early_live": early_live,
             "health_changes": health,
             "sockets": w["socks"][n0 - 1:], "false_reads": false_reads, "qualifying_ok_read": qualifying_ok,
             "breaks_line": after["breaks"], "line2": {p: after["graphs"][p]["line2"] for p in paths},
             "expected_gaps": expected_gaps, "states_on_fault_socket_after_fault": len(late_on_fault_sock),
             "gaps": {p: [before["graphs"][p]["gaps"], after["graphs"][p]["gaps"]] for p in paths},
             "segments": {p: [before["graphs"][p]["segments"], after["graphs"][p]["segments"]] for p in paths}})
        # The extra measured case (review finding): log rows with a break note in line 2.
        await m.cdp.viewport(*WIDE)
        await m.cdp.js("window.scrollTo(0, 0)")
        # The shared line shows the break, the log has more rows than fit, and two frames have laid it out.
        await m.cdp.wait_for("!document.getElementById('graphs-breaks').hidden && "
                             "document.querySelectorAll('#log-body > tr').length > 10", timeout=10)
        await m.settled()
        rows = await m.cdp.js(LOG_ROWS)
        await m.shot("m3b-d-log-rows-with-break-note.png")
        noted = re.search(GAP_NOTE, rows["breaks"])
        m.cases.record(
            "Log rows at 1440 x 900, graphs open, a break note showing (extra, encoding break)",
            "At least 5 full log rows inside #logwrap at 1440 x 900 with the graphs open and a break note showing "
            "(the shared line under the graphs head: No data from t = A to t = B s (...))",
            {"a break note is shown": noted is not None, "at least 5 full log rows": rows["full"] >= 5},
            rows)
        run.log("stop traffic")
        run.stop(traffic)
        traffic = None
    finally:
        run.stop(traffic)
        run.stop(sim)


async def part_e_same_value(run: Run, m: M3b) -> None:
    """ice_default.yaml (no scenario): full-snapshot faults by wall time, then a DTC-only fault."""
    sim = await m3b_start(run, DEFAULT_PROFILE, "e-fault-server.log",
                          ["--state-fault", "20:25", "--state-fault", "45:55", "--state-fault", "75:85:dtcs"])
    try:
        await m.open_page()

        async def uptime() -> float:
            return float((await m.status())["uptime_s"])

        async def until_uptime(t: float) -> None:
            while (u := await uptime()) < t:
                await asyncio.sleep(min(0.2, t - u))

        # Same-value recovery: a page loaded before the fault.
        await until_uptime(19.0)
        states0 = len((await m.wrapper())["states"])
        samples, _ = await m.until(lambda s: False, timeout=12.0)
        t_end_wall = time.time() * 1000 - (await uptime() - 25.0) * 1000      # wall ms at uptime 25
        w = await m.wrapper()
        during = [s for s in samples if s["reason"] == "encoding"]
        live_after = next((s for s in samples if during and s["now"] > during[-1]["now"] and s["health"] == "live"),
                          None)
        polls = [s["polls"] for s in during]
        late_states = [x for x in w["states"][states0:] if x["t"] >= t_end_wall - 300]
        await m.shot("m3b-e-same-value-recovered.png")
        conds = {
            "data-reason encoding during the fault": bool(during),
            "data-polls rising during it": len(set(polls)) >= 2,
            "live within one poll plus one resync (<= 5 s) after it": live_after is not None
            and (live_after["now"] - t_end_wall) / 1000 <= 5.0,
            "the wrapper saw a state after the recovery": bool(late_states),
        }
        observed: dict[str, Any] = {
            "fault_samples": len(during), "polls": [polls[:1], polls[-1:]],
            "live_after_fault_end_s": (live_after["now"] - t_end_wall) / 1000 if live_after else None,
            "states_after_recovery": late_states, "sockets": w["socks"][-3:]}
        # A page loaded during the fault (45-55) starts last known, although it gets a state after hello.
        await until_uptime(47.0)
        await m.cdp.send("Page.navigate", url=PAGE)
        await m.cdp.wait_for("document.body && document.body.dataset.conn === 'live' && window.__m3b"
                             " && window.__m3b.states.length >= 1", timeout=10)
        loaded: list[dict[str, Any]] = []
        while await uptime() < 54.5:
            loaded.append(await m.state())
            await asyncio.sleep(0.1)
        await m.shot("m3b-e-loaded-during-fault.png")
        rec, ok = await m.until(lambda s: s["health"] == "live", timeout=10.0)
        # The new document's own attribute log: no health -> live change before the fault ended.
        t55 = time.time() * 1000 - (await uptime() - 55.0) * 1000
        health = [x for x in await m.attrs(0) if x["a"] == "health"]
        early = [x for x in health if x["v"] == "live" and x["t"] < t55]
        conds["... and its attribute log has no change to live before the fault ended"] = bool(health) and not early
        observed["loaded_health_changes"] = health
        observed["fault_end_ms"] = t55
        conds["a page loaded during the fault: last known, not Live"] = bool(loaded) and all(
            s["health"] == "last-known" and s["reason"] == "encoding" and s["text"] != "Live" for s in loaded)
        conds["... although it received a state after hello"] = all(s["states"] >= 1 for s in loaded)
        conds["... and Live after the fault ends"] = ok
        observed["loaded_during"] = [slim(loaded[0]), slim(loaded[-1])] if loaded else None
        observed["loaded_live_after"] = slim(rec[-1])
        m.cases.record(
            "Same-value recovery",
            "Fault server, no scenario: the snapshot raises for 5 s, then recovers to identical text. data-reason "
            "encoding during the fault, with data-polls rising; after it, live within one poll plus one resync "
            "(<= 5 s); the wrapper saw a state after the recovery. A page loaded during the fault starts as last "
            "known, not Live, although it received a state after hello", conds, observed)

        # REST is not proof: only the DTC part fails (75-85).
        await until_uptime(73.0)
        rest: list[int] = []
        page: list[dict[str, Any]] = []
        while await uptime() < 85.0:
            async with m.http.get(f"http://{API}/api/v1/vehicle") as resp:
                rest.append(resp.status)
            page.append(await m.state())
            await asyncio.sleep(0.25)
        during = [s for s in page if s["reason"] == "encoding"]
        first = during[0] if during else None
        after_first = [s for s in page if first and s["now"] >= first["now"]]
        rec, ok = await m.until(lambda s: s["health"] == "live", timeout=10.0)
        t85 = time.time() * 1000 - (await uptime() - 85.0) * 1000
        async with m.http.get(f"http://{API}/api/v1/vehicle") as resp:
            rest.append(resp.status)
        await m.shot("m3b-e-rest-not-proof-recovered.png")
        m.cases.record(
            "REST is not proof",
            "Fault server: the DTC part of the snapshot fails and the vehicle part succeeds. GET /vehicle answers 200 "
            "throughout; the page stays last known until the fault ends, and then clears as above",
            {"GET /vehicle 200 throughout": bool(rest) and all(x == 200 for x in rest),
             "the page goes last known (encoding)": first is not None,
             "and stays last known until the fault ends": bool(after_first)
             and all(s["health"] == "last-known" for s in after_first),
             "then clears": ok and rec[-1]["now"] >= t85 - 300},
            {"vehicle_statuses": sorted(set(rest)), "requests": len(rest),
             "first_last_known": slim(first) if first else None, "live_after_s": (rec[-1]["now"] - t85) / 1000})
    finally:
        run.stop(sim)


async def m3b_session(run: Run, chrome: str, profile_dir: str) -> None:
    await launch_chrome(run, chrome, profile_dir, ("--enable-precise-memory-info",))
    cases = Cases(run)
    problems: list[str] = []
    version = "unknown"
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        version = await chrome_version(http)
        run.log(f"Chrome: {version}")
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = await m3b_cdp(run, http, ws, problems)
            m = M3b(run, cdp, http, cases)
            try:
                await part_a(run, m)
                await part_b(run, m)
                await part_c_nonfinite(run, m)
                await part_d_encoding(run, m)
                await part_e_same_value(run, m)
            finally:
                cases.extra["problems"] = problems
                cases.write(version)
                run.log(f"console messages, exceptions and browser log entries: {len(problems)}")
                for problem in problems:
                    run.log(f"  {problem}")
                cdp.reader.cancel()
    run.log("done")


async def m3b_slots_session(run: Run, chrome: str, profile_dir: str) -> None:
    """The four-client variants A, B and C alone (part A's simulator and page), to repeat them."""
    await launch_chrome(run, chrome, profile_dir)
    cases = Cases(run)
    problems: list[str] = []
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        version = await chrome_version(http)
        run.log(f"Chrome: {version}")
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = await m3b_cdp(run, http, ws, problems)
            m = M3b(run, cdp, http, cases)
            sim = await m3b_start(run, DEFAULT_PROFILE, "slots-simulator.log")
            try:
                await m.open_page()
                await run_slot_cases(run, m)
            finally:
                run.stop(sim)
                passed = sum(1 for c in cases.items if c["pass"])
                out = {"chrome": version, "cases": cases.items, "problems": problems,
                       "tally": {"passed": passed, "total": len(cases.items)}}
                (run.outdir / "m3b-slots-results.json").write_text(json.dumps(out, indent=1, default=str))
                run.log(f"slot cases: {passed} of {len(cases.items)} passed")
                cdp.reader.cancel()
    run.log("done")


async def m3b_long_session(run: Run, chrome: str, profile_dir: str) -> None:
    """Bounded history: about 11 min on ice_scenario.yaml with traffic, the 10 min window selected."""
    await launch_chrome(run, chrome, profile_dir)
    cases = Cases(run)
    problems: list[str] = []
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        version = await chrome_version(http)
        run.log(f"Chrome: {version}")
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = await m3b_cdp(run, http, ws, problems)
            m = M3b(run, cdp, http, cases)
            sim = await m3b_start(run, SCENARIO_PROFILE, "long-simulator.log")
            traffic = None
            try:
                await m.open_page()
                await m.click_window(600)
                traffic = start_traffic(run)
                samples: list[dict[str, Any]] = []
                t0 = time.monotonic()
                while True:
                    s = await m.state()
                    samples.append(s)
                    as_of = float(next(iter(s["graphs"].values()))["asOf"] or 0)
                    if len(samples) % 6 == 1:
                        run.log(f"long: as_of {as_of:.1f}, points " + json.dumps(graph_vals(s, "points")))
                    if as_of >= 690 or time.monotonic() - t0 > 760:
                        break
                    await asyncio.sleep(max(0.0, t0 + 10 * len(samples) - time.monotonic()))
                await m.shot("m3b-long-10min.png")
            finally:
                run.stop(traffic)
                run.stop(sim)
            paths = [p for p, g in samples[0]["graphs"].items() if not g["hidden"]]
            first_t = min(float(samples[0]["graphs"][p]["oldestT"] or 0) for p in paths)

            def num(s: dict[str, Any], p: str, k: str) -> float:
                return float(s["graphs"][p][k])

            late = [s for s in samples if num(s, paths[0], "asOf") >= 610]
            cool = "engine.coolant_temp"
            cool_old = [s for s in late if num(s, cool, "oldestT") < num(s, cool, "asOf") - 600]
            cases.record(
                "Bounded history",
                "--long on ice_scenario.yaml with traffic, the 10 min window selected. At every 10 s sample, for every "
                "graph: data-points <= data-cap and data-points-before-window <= 1. After 600 s: "
                "data-points-before-window = 1 and data-left-value set. Coolant (constant late in the run) shows "
                "data-points-before-window <= 1 while its data-oldest-t is older than the window",
                {"window 600 everywhere": all(g["windowS"] == "600" for s in samples for g in s["graphs"].values()
                                              if not g["hidden"]),
                 "points <= cap at every sample": all(int(s["graphs"][p]["points"]) <= int(s["graphs"][p]["cap"])
                                                      == CAP for s in samples for p in paths),
                 "points-before-window <= 1 at every sample": all(int(s["graphs"][p]["pointsBeforeWindow"]) <= 1
                                                                  for s in samples for p in paths),
                 "after 600 s: points-before-window = 1 and left-value set": bool(late) and all(
                     s["graphs"][p]["pointsBeforeWindow"] == "1" and s["graphs"][p]["leftValue"] != ""
                     for s in late for p in paths),
                 "coolant: <= 1 before the window while its oldest-t is older than the window": bool(cool_old)
                 and all(int(s["graphs"][cool]["pointsBeforeWindow"]) <= 1 for s in cool_old)},
                {"samples": len(samples), "first_t": first_t,
                 "last_as_of": num(samples[-1], paths[0], "asOf"), "late_samples": len(late),
                 "coolant_old_samples": len(cool_old),
                 "max_points": {p: max(int(s["graphs"][p]["points"]) for s in samples) for p in paths},
                 "last": {p: {k: samples[-1]["graphs"][p][k] for k in ("points", "cap", "oldestT", "asOf",
                                                                       "pointsBeforeWindow", "leftValue")}
                          for p in paths},
                 "series": [{"as_of": num(s, paths[0], "asOf"), "points": graph_vals(s, "points"),
                             "before": graph_vals(s, "pointsBeforeWindow"), "oldest": graph_vals(s, "oldestT")}
                            for s in samples]})
            out = {"chrome": version, "cases": cases.items, "problems": problems}
            (run.outdir / "m3b-long-results.json").write_text(json.dumps(out, indent=1, default=str))
            cdp.reader.cancel()
    run.log("done")


def find_chrome() -> str:
    for name in (os.environ.get("CHROME", ""), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if name and (path := shutil.which(name)):
            return path
    raise SystemExit("no Chrome or Chromium found; set CHROME=/path/to/chrome")


def require_private_namespace() -> None:
    """Refuse to run on the host network: it would start a simulator on the host's vcan0
    and take 127.0.0.1:8765. run_gui_demo.sh records the host's network namespace before
    unshare; this process must be in a different one."""
    host = os.environ.get("GUI_DEMO_HOST_NETNS")
    here = os.readlink("/proc/self/ns/net")
    if not host or host == here:
        raise SystemExit("gui_demo_capture.py runs only inside scripts/run_gui_demo.sh's private "
                         f"network namespace (host {host or 'unknown'}, here {here}); refusing")


def main(argv: list[str] | None = None) -> int:
    require_private_namespace()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("outdir", type=Path, help="directory for the screenshots and logs")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--moving", action="store_true",
                       help="the moving-vehicle set (docs/examples/ice_drive_cycle_stepped.yaml)")
    modes.add_argument("--m3b", action="store_true",
                       help="the M3b browser checks (design §12.2) and the cost measurement (§11.3)")
    modes.add_argument("--m3b-long", action="store_true",
                       help="the M3b bounded-history case: about 11 min on ice_scenario.yaml")
    modes.add_argument("--m3b-slots", action="store_true",
                       help="the M3b four-client variants A, B and C alone (about 1.5 min)")
    args = parser.parse_args(argv)
    mode = ("moving" if args.moving else "m3b" if args.m3b else "m3b-long" if args.m3b_long
            else "m3b-slots" if args.m3b_slots else "m3a")
    args.outdir.mkdir(parents=True, exist_ok=True)
    run = Run(args.outdir.resolve())
    run.log(f"gui demo capture, {datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')}")

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        asyncio.run(demo(run, find_chrome(), mode))
    except KeyboardInterrupt:
        print("interrupted; every process was stopped", file=sys.stderr)
        return 130
    if run.case_failures:
        print("M3b cases failed: " + "; ".join(run.case_failures), file=sys.stderr)
        return 1
    if run.overflow_failures:
        print("horizontal overflow at: " + "; ".join(run.overflow_failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
