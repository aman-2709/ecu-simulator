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
--m3b-perf is the main-thread investigation: equivalent 60 s traced runs (log running, paused,
cleared; graphs hidden; no traffic) at 1440 x 900 and 390 x 844, broken down by
scripts/gui_trace_breakdown.py into m3b-perf-results.json. --m3b-perf-log is the log fix's
before / after measurement: the same set-up, the log following, with real clicks (Pause,
Resume, a filter, Older, Jump to newest) during each traced run and their Event Timing, into
m3b-perf-log-results.json. --m3b-log runs the windowed exchange log's cases (following, pause,
filters, Older / Newer, eviction, markers, expansion, wording, layout) at 1440 x 900 and
390 x 844, each from a full 2,000-exchange buffer, into m3b-log-results.json.
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
# the control's box intersects the log box. Since the windowed log (Task 43) a pinned window
# ends with a Newer row (tr.lognav, in its own tbody after #log-body: the window's navigation,
# not a log row): it is not counted, and `beyond` is the control's "+ M beyond this window"
# (matching exchanges after the window, not drawn), read from its text.
JUMP_STATE = """(function(){
  var b = document.getElementById('btn-follow'), w = document.getElementById('logwrap');
  var wr = w.getBoundingClientRect(), br = b.getBoundingClientRect();
  var rows = document.querySelectorAll('#log-body > tr:not(.lognav)'), counted = 0, partial = 0;
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i].getBoundingClientRect();
    if (r.height === 0) continue;
    if (r.top >= wr.bottom - 1) counted++; else if (r.bottom > wr.bottom + 1) partial++;
  }
  var shown = !b.classList.contains('is-off') && getComputedStyle(b).visibility !== 'hidden';
  var overlap = shown && br.left < wr.right && br.right > wr.left && br.top < wr.bottom && br.bottom > wr.top;
  var m = /^([0-9,]+) rows? below/.exec(b.textContent);
  var k = /([0-9,]+)(?: rows?)? beyond this window/.exec(b.textContent);
  return {shown: shown, text: b.textContent, n: m ? Number(m[1].replace(/,/g, '')) : null,
          beyond: k ? Number(k[1].replace(/,/g, '')) : 0,
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
                "m3b-slots": m3b_slots_session, "m3b-perf": m3b_perf_session,
                "m3b-perf-log": m3b_perf_log_session, "m3b-log": m3b_log_session}
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
  var T = window.__m3b = { socks: [], states: [], status: [], badFirst: false, invalidPath: null, lastState: null,
                              incompleteFirst: 0 };
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
                frames: 0, states: 0, bad: null, incomplete: null };
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
        if (m && m.type === "state") {
          rec.states += 1; T.states.push({ t: Date.now(), sock: i, len: e.data.length });
          T.lastState = e.data;           // the latest real state frame, for T.send's variants
          // Test only (Task 41): while T.incompleteFirst > 0, a socket's first state reaches the
          // page without dtcs, once per count: an incomplete state during a recovery attempt.
          if (rec.states === 1 && T.incompleteFirst > 0) {
            T.incompleteFirst -= 1;
            delete m.dtcs;
            rec.incomplete = Date.now();
            e.stopImmediatePropagation();
            var inc = new MessageEvent("message", { data: JSON.stringify(m) }); inc.__synthetic = true;
            ws.dispatchEvent(inc);
            return;
          }
        }
        // Test only (Task 37's forced-wrap reading): while T.invalidPath is set, each state reaches
        // the page with that signal sent as null and listed in nonfinite, as the API sends a
        // non-finite value (§8.2). The simulator's values stay finite, so traffic can run.
        if (T.invalidPath && m && m.type === "state" && m.vehicle && m.vehicle.signals) {
          m.vehicle.signals[T.invalidPath] = null;
          m.vehicle.nonfinite = (m.vehicle.nonfinite || []).concat([T.invalidPath]).sort();
          e.stopImmediatePropagation();
          var ev = new MessageEvent("message", { data: JSON.stringify(m) }); ev.__synthetic = true;
          ws.dispatchEvent(ev);
        }
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
  // Any text as one synthetic frame on the page's latest socket (Task 41: an incomplete state,
  // an unknown message type). T.sent records each one.
  T.sent = [];
  T.send = function (text) {
    var i = T.socks.length - 1;
    var ev = new MessageEvent("message", { data: text }); ev.__synthetic = true;
    T.sent.push({ t: Date.now(), sock: i, text: text.slice(0, 80) });
    live[i].dispatchEvent(ev);
  };
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
# The owner's restart note (Task 37), complete; it leads the shared notice line.
RESTART_NOTE = r"Simulator restarted at \d\d:\d\d:\d\d UTC; previous graph history cleared\."
INVALID_RUN = r"Throttle: invalid value from t = [0-9.]+ to t = [0-9.]+ s \(still invalid\)"


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
                  problems: list[str], wrapper: bool = True) -> DevTools:
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
    if wrapper:
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


# Task 41: a recognised state whose vehicle or dtcs is not an object is a data fault (the owner's
# finding: the page used to ignore it and stay Live). The frame is the page's latest real state
# with one part removed, dispatched on the page's socket by the wrapper (T.send).
INCOMPLETE = ("(() => { const m = JSON.parse(window.__m3b.lastState); delete m.%s;"
              " window.__m3b.send(JSON.stringify(m)); return %s; })()")


async def case_incomplete_state(m: M3b) -> None:
    before = await m.status()
    results: dict[str, Any] = {}
    conds: dict[str, bool] = {}
    for drop in ("dtcs", "vehicle"):
        await asyncio.sleep(1.0)
        w0 = await m.wrapper()
        pre = await m.state()
        imm = await m.cdp.js(INCOMPLETE % (drop, PAGE_STATE))
        samples, ok = await m.until(lambda s: s["health"] == "live", timeout=6.0)
        await asyncio.sleep(0.6)                   # the log re-renders at most every 200 ms
        end = await m.state()
        changes = await m.attrs(imm["now"])
        w = await m.wrapper()
        new_socks = w["socks"][len(w0["socks"]):]
        first_new_state = next((x["t"] for x in w["states"] if x["sock"] >= len(w0["socks"])), None)
        first_live = next((x["t"] for x in changes if x["a"] == "health" and x["v"] == "live"), None)
        tag = f"missing {drop}"
        conds[f"{tag}: healthy before"] = pre["health"] == "live" and pre["episode"] == "none"
        conds[f"{tag}: immediately last-known/malformed/active, malformed +1"] = (
            (imm["conn"], imm["data"], imm["reason"], imm["episode"]) == ("live", "last-known", "malformed", "active")
            and imm["malformed"] == pre["malformed"] + 1)
        conds[f"{tag}: immediately the Last known tag"] = any(t.startswith("Last known") for t in imm["known"])
        conds[f"{tag}: the banner names an incomplete state message"] = (
            "incomplete state message" in (imm["banner"] or ""))
        conds[f"{tag}: Live only after the valid state on the new socket"] = (
            ok and bool(new_socks) and first_new_state is not None and first_live is not None
            and first_live >= first_new_state)
        conds[f"{tag}: after: live, episode none, attempts 0, no tag"] = (
            (end["health"], end["episode"], end["attempts"], end["known"]) == ("live", "none", 0, []))
        results[tag] = {"before": slim(pre), "immediately": slim(imm), "end": slim(end),
                        "live_after_s": (samples[-1]["now"] - imm["now"]) / 1000, "new_sockets": new_socks,
                        "first_new_state_t": first_new_state, "first_live_t": first_live, "attribute_changes": changes}
    lg = await m.log()
    after = await m.status()
    conds["the log names the incomplete state"] = (
        lg["text"].count("Resynchronised after an incomplete state message") == 2)
    conds["no restart marker"] = "Simulator restarted" not in lg["text"]
    conds["started_at unchanged"] = before["started_at"] == after["started_at"]
    results["log_marks"] = lg["marks"]
    m.cases.record(
        "Incomplete state, then unchanged data",
        "On a healthy page, a recognised state missing dtcs (then one missing vehicle): immediately data-conn live, "
        "data-data last-known, data-reason malformed, data-episode active, data-malformed-total +1, the Last known "
        "tag, a banner naming an incomplete state message. The resync's new socket delivers unchanged valid data; "
        "Live only after that valid state (the first health=live change is not before the new socket's first state); "
        "then data-episode none, data-attempts 0; the log names the incomplete state; no restart marker; "
        "started_at unchanged",
        conds, results)
    await m.shot("m3b-a2b-incomplete-recovered.png")


async def case_unknown_type(m: M3b) -> None:
    pre = await m.state()
    imm = await m.cdp.js("(() => { window.__m3b.send('{\"type\":\"future\"}'); return " + PAGE_STATE + "; })()")
    await asyncio.sleep(2.5)
    later = await m.state()
    changes = await m.attrs(imm["now"] - 1)

    def same(s: dict[str, Any]) -> bool:
        return ((s["text"], s["health"], s["episode"], s["malformed"], s["socks"], s["banner"])
                == ("Live", "live", "none", pre["malformed"], pre["socks"], None))

    m.cases.record(
        "Unknown message type ignored",
        "A {\"type\":\"future\"} frame on a healthy page changes nothing: still Live, data-malformed-total unchanged, "
        "data-episode none, no new socket, no banner, immediately and 2.5 s later",
        {"healthy before": pre["health"] == "live", "immediately unchanged": same(imm),
         "2.5 s later unchanged": same(later),
         "no health attribute change": not any(x["a"] in ("health", "episode") for x in changes)},
        {"before": slim(pre), "immediately": slim(imm), "later": slim(later), "attribute_changes": changes})


async def case_incomplete_in_attempt(m: M3b) -> None:
    await asyncio.sleep(1.0)
    w0 = await m.wrapper()
    n0 = len(w0["socks"])
    marks0 = len((await m.log())["marks"])     # this case's marks are the ones after these
    await m.cdp.js("window.__m3b.incompleteFirst = 1")
    imm = await m.cdp.js("(() => { window.__m3b.bad(); return " + PAGE_STATE + "; })()")
    samples, ok = await m.until(lambda s: s["health"] == "live", timeout=10.0)
    await asyncio.sleep(0.6)
    end = await m.state()
    changes = await m.attrs(imm["now"])
    w = await m.wrapper()
    attempts = w["socks"][n0:]
    lg = await m.log()
    own = [x for x in lg["marks"][marks0:] if x.startswith("Resynchronised after")]
    m.cases.record(
        "Incomplete state during an attempt",
        "An unreadable frame starts an episode; the first attempt's socket delivers an incomplete state (the wrapper "
        "removes dtcs from that socket's first state). That ends the attempt (data-attempts 1, then 2 after the "
        "2 s wait); the second attempt's valid state recovers: Live, data-episode none, data-attempts 0, and the "
        "resync line names both causes",
        {"immediately active": imm["episode"] == "active",
         "two attempt sockets, the first got the incomplete state": len(attempts) == 2
         and attempts[0]["incomplete"] is not None and attempts[1]["incomplete"] is None,
         "the second attempt started about 2 s after the first ended (+-0.3)": len(attempts) == 2
         and abs((attempts[1]["created"] - attempts[0]["incomplete"]) / 1000 - 2.0) <= 0.3,
         "data-attempts went 1, 2, then 0": [int(x["v"]) for x in changes if x["a"] == "attempts"] == [1, 2, 0],
         "recovered: live, episode none, attempts 0": ok and (end["health"], end["episode"], end["attempts"])
         == ("live", "none", 0),
         # The resync mark this case added (the last one after its fault) names both causes.
         "this case's resync line names both causes": bool(own) and own[-1].startswith(
             "Resynchronised after an unreadable message and an incomplete state message.")},
        {"immediately": slim(imm), "end": slim(end), "attempt_sockets": attempts, "attribute_changes": changes,
         "live_after_s": (samples[-1]["now"] - imm["now"]) / 1000, "this_case_resync_marks": own})


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
        await case_incomplete_state(m)
        await case_unknown_type(m)
        await case_incomplete_in_attempt(m)
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
    rows = await _rows_readings(m, "m3b-b-restart-and-gap-1440.png")
    m.cases.record(
        "Log rows at 1440 x 900, restart note and a gap note together (measured)",
        "At 1440 x 900 with the graphs open, the restart note and a gap note visible together: at least 5 full "
        "log rows inside #logwrap",
        # Since Task 37 the restart note, in the owner's short wording, leads the shared notice line,
        # " · "-joined with the gap; both are checked complete.
        {"the restart note is shown, complete": re.match(RESTART_NOTE, rows["breaks"]) is not None,
         "a gap note is shown, complete": re.search(GAP_NOTE, rows["breaks"]) is not None,
         "the newest rows are exchange rows": rows["fullExchange"] == rows["full"],
         "the log is filled (more rows than fit)": len((await m.log())["seqs"]) > rows["full"],
         "at least 5 full log rows": rows["full"] >= 5}, rows)
    # The forced-wrap reading (Task 37, owner: "including wrapping"): an open invalid run added to
    # the same line, by the wrapper rewriting each state (test only), so the line wraps to two.
    await m.cdp.js("window.__m3b.invalidPath = 'engine.throttle'")
    try:
        await m.until(lambda s: re.search(INVALID_RUN, s["breaks"]) is not None, timeout=6.0)
        await m.cdp.wait_for("[...document.querySelectorAll('#log-body > tr')].slice(-6)"
                             ".every(r => r.querySelector('td.c-seq'))", timeout=15)
        wrapped = await _rows_readings(m, "m3b-b-restart-gap-invalid-1440.png")
    finally:
        await m.cdp.js("window.__m3b.invalidPath = null")
    m.cases.record(
        "Log rows at 1440 x 900, restart note, a gap and an open invalid run: the notice line wraps (measured)",
        "At 1440 x 900 with the graphs open, the restart note, a gap note and an open invalid run on the shared "
        "notice line, which wraps to two lines, every notice complete: at least 5 full log rows inside #logwrap",
        {"the restart note is shown, complete": re.match(RESTART_NOTE, wrapped["breaks"]) is not None,
         "a gap note is shown, complete": re.search(GAP_NOTE, wrapped["breaks"]) is not None,
         "the open invalid run is shown, complete": re.search(INVALID_RUN, wrapped["breaks"]) is not None,
         "the notice line wraps (two lines or more)": wrapped["breaksLines"] >= 2,
         "the newest rows are exchange rows": wrapped["fullExchange"] == wrapped["full"],
         "at least 5 full log rows": wrapped["full"] >= 5}, wrapped)


async def _rows_readings(m: M3b, shot: str) -> dict[str, Any]:
    """Three readings 1 s apart, the screenshot straight after the last; the result is the fewest
    rows (the worst moment), with every reading recorded."""
    await m.cdp.js("window.scrollTo(0, 0)")
    await m.settled()
    readings = []
    for k in range(3):
        if k:
            await asyncio.sleep(1.0)       # spacing between readings; no page condition to wait on
        await m.settled()
        readings.append(await m.cdp.js(LOG_ROWS))
    await m.shot(shot)
    readings.append(await m.cdp.js(LOG_ROWS))
    rows = dict(min(readings, key=lambda r: (r["full"], r["rowsRegion"])))
    rows["readings"] = readings
    return rows


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
         "restart note visible": re.match(RESTART_NOTE, s["breaks"]) is not None,
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


# ---- --m3b-perf: the main-thread investigation (Task 39) ----
# Equivalent 60 s runs on the stepped demo with the traffic script at 4/s, with the log held at
# its cap (MAX_ROWS = 2000 exchanges in app.js) from start to end, so every run has the same
# retained rows. No WebSocket wrapper and no MutationObserver: the page runs alone, and the
# capture evaluates in the page only at each run's start and end.
PERF_ROWS = 2000                 # app.js MAX_ROWS
PERF_PREFILL_RATE = "50"         # the traffic script's maximum, only to reach the cap sooner
PERF_SECONDS = 60.0
PERF_CATEGORIES = ["toplevel", "devtools.timeline", "v8"]
# In this order at each viewport. "log-cleared" uses the page's own Clear view: the 2000
# exchanges stay retained, and only those arriving after the clear are rendered. The
# reduced-motion runs emulate prefers-reduced-motion, which app.css answers by turning every
# animation off (the live lamp's endless beat and the signal table's change flash).
PERF_CONDITIONS = ("baseline-a", "log-paused", "graphs-hidden", "no-traffic", "no-traffic-reduced-motion",
                   "baseline-b", "log-cleared", "log-cleared-reduced-motion")
PAUSED = "document.getElementById('btn-pause').getAttribute('aria-pressed') === 'true'"
GRAPHS_HIDDEN = "document.getElementById('graphs').hidden"
# The log as the page holds it: retained exchanges (the ECU filter's "All ECUs (N)", which is
# the page's S.exCount), the table's rows, the exchange rows among them, and the last seq.
PERF_LOG = """(() => {
  const all = document.getElementById('f-ecu').options[0].textContent;
  const m = /last seq (\\d+)/.exec(document.getElementById('log-count').textContent);
  return {retained: Number((/\\((\\d+)\\)/.exec(all) || [0, -1])[1]),
          dom_rows: document.querySelectorAll('#log-body > tr').length,
          exchange_rows: document.querySelectorAll('#log-body > tr.ex').length,
          last_seq: m ? Number(m[1]) : null, health: document.body.dataset.health,
          graphs_hidden: document.getElementById('graphs').hidden,
          log_paused: document.getElementById('btn-pause').getAttribute('aria-pressed') === 'true',
          log_count: document.getElementById('log-count').textContent};
})()"""


def traffic_sent(run: Run) -> int:
    """The traffic script's own request counter, from the last numbered line of its log."""
    path = run.outdir / "traffic.log"
    if not path.exists():
        return 0
    for line in reversed(path.read_text(errors="replace").splitlines()):
        head = line.split(maxsplit=1)
        if head and head[0].isdigit():
            return int(head[0])
    return 0


async def perf_click(cdp: DevTools, selector: str, check: str, want: bool) -> None:
    """A real click on a view control, brought into view first (at 390 px the log's controls are
    below the fold), then a check that the page took it."""
    await cdp.js(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: 'center'}})")
    await asyncio.sleep(0.3)
    await cdp.click(selector)
    await cdp.wait_for(f"({check}) === {json.dumps(want)}", 10)


async def reduced_motion(cdp: DevTools, on: bool) -> None:
    features = [{"name": "prefers-reduced-motion", "value": "reduce"}] if on else []
    await cdp.send("Emulation.setEmulatedMedia", features=features)
    await cdp.wait_for(f"matchMedia('(prefers-reduced-motion: reduce)').matches === {json.dumps(on)}", 5)


async def perf_trace(run: Run, cdp: DevTools, label: str, condition: str, size: tuple[int, int],
                     traffic_on: bool, lines: dict[str, int] | None = None,
                     during: Callable[[], Any] | None = None) -> dict[str, Any]:
    """One 60 s traced run. ``lines``: the app.js callback lines of the page served (default:
    the working tree's). ``during``: a coroutine function run alongside the 60 s (the
    log-fix mode's interactions); its result is recorded as ``interactions``."""
    import hashlib

    sys.path.insert(0, str(ROOT / "scripts"))
    import gui_trace_breakdown

    async def metrics() -> dict[str, float]:
        res = await cdp.send("Performance.getMetrics")
        return {x["name"]: x["value"] for x in res["metrics"]}

    complete: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    def traced(params: dict[str, Any]) -> None:
        if not complete.done():
            complete.set_result(params)

    cdp.handlers["Tracing.tracingComplete"] = traced
    await cdp.send("Performance.enable")
    start = await cdp.js(PERF_LOG)
    sent0 = traffic_sent(run)
    await cdp.send("Tracing.start", transferMode="ReturnAsStream", streamCompression="gzip",
                   traceConfig={"includedCategories": PERF_CATEGORIES})
    m0 = await metrics()
    t0 = time.monotonic()
    interactions = None
    if during is None:
        await asyncio.sleep(PERF_SECONDS)
    else:
        interactions = (await asyncio.gather(asyncio.sleep(PERF_SECONDS), during()))[1]
    m1 = await metrics()
    elapsed = time.monotonic() - t0
    await cdp.send("Tracing.end")
    sent1 = traffic_sent(run)
    end = await cdp.js(PERF_LOG)
    done = await asyncio.wait_for(complete, 120)
    trace_path = run.outdir / f"perf-{label}.json.gz"
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
    seqs = (start["last_seq"], end["last_seq"])
    result = {
        "label": label, "viewport": list(size), "condition": condition, "seconds": round(elapsed, 2),
        "traffic": {"on": traffic_on, "sent_start": sent0, "sent_end": sent1,
                    "requests_per_s": round((sent1 - sent0) / elapsed, 2) if traffic_on else 0.0},
        "exchanges_per_s": round((seqs[1] - seqs[0]) / elapsed, 2) if None not in seqs else None,
        "log_start": start, "log_end": end,
        "metrics_s": {k: round(m1[k] - m0[k], 3) for k in ("TaskDuration", "ScriptDuration", "LayoutDuration",
                                                           "RecalcStyleDuration")},
        "trace_file": trace_path.name, "trace_bytes": trace_path.stat().st_size,
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        "breakdown": gui_trace_breakdown.summarise(trace_path, lines or gui_trace_breakdown.app_lines(None), elapsed),
    }
    if interactions is not None:
        result["interactions"] = interactions
    b = result["breakdown"]
    run.log(f"perf {label}: busy {b['busy_percent']} % ({b['busy_ms']} ms), split {b['split_ms']}, renderLog "
            f"{b['callbacks']['renderLog']['count']} / {b['callbacks']['renderLog']['sum_ms']} ms (p95 "
            f"{b['callbacks']['renderLog']['p95_ms']}), long tasks {b['long_tasks']['count']} (max "
            f"{b['long_tasks']['max_ms']} ms), rows "
            f"{start['retained']}->{end['retained']}, {result['traffic']['requests_per_s']} req/s, "
            f"{result['exchanges_per_s']} exchanges/s")
    return result


async def m3b_perf_session(run: Run, chrome: str, profile_dir: str) -> None:
    """The comparison runs of Task 39: per viewport, the log running (twice), paused, cleared,
    the graphs hidden, and no traffic; each 60 s with a trace, at the log's row cap."""
    await launch_chrome(run, chrome, profile_dir)
    problems: list[str] = []
    runs: list[dict[str, Any]] = []
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        version = await chrome_version(http)
        run.log(f"Chrome: {version}")
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = await m3b_cdp(run, http, ws, problems, wrapper=False)
            m = M3b(run, cdp, http, Cases(run))
            sim = await m3b_start(run, MOVING_PROFILE, "perf-simulator.log")
            traffic = None
            try:
                await m.open_page()
                await m.click_window(120)
                run.log(f"prefill: start traffic at {PERF_PREFILL_RATE}/s until the log holds {PERF_ROWS} exchanges")
                traffic = run.spawn([sys.executable, str(ROOT / "scripts" / "gui_demo_traffic.py"), "--interface",
                                     IFACE, "--rate", PERF_PREFILL_RATE], "prefill-traffic.log")
                deadline = time.monotonic() + 300
                while (await cdp.js(PERF_LOG))["retained"] < PERF_ROWS:
                    if time.monotonic() > deadline:
                        raise RuntimeError("the log did not reach its cap")
                    await asyncio.sleep(2.0)
                run.stop(traffic)
                traffic = start_traffic(run)
                await asyncio.sleep(5.0)
                run.log(f"prefilled: {await cdp.js(PERF_LOG)}")
                for vlabel, size in (("1440x900", WIDE), ("390x844", NARROW)):
                    await cdp.viewport(*size)
                    await asyncio.sleep(1.5)
                    for condition in PERF_CONDITIONS:
                        quiet = condition.startswith("no-traffic")
                        if quiet:
                            run.log("stop traffic")
                            run.stop(traffic)
                            traffic = None
                        if condition.endswith("reduced-motion"):
                            await reduced_motion(cdp, True)
                        if condition == "log-paused":
                            await perf_click(cdp, "#btn-pause", PAUSED, True)
                        elif condition == "graphs-hidden":
                            await perf_click(cdp, "#btn-graphs-toggle", GRAPHS_HIDDEN, True)
                        elif condition.startswith("log-cleared"):
                            await perf_click(cdp, "#btn-clear", "document.querySelectorAll('#log-body > tr.ex')"
                                             ".length < 50", True)
                        # As in measure_cost: at 390 px the graphs at the top of the viewport.
                        await cdp.js("document.getElementById('graphs-panel').scrollIntoView({block: 'start'})"
                                     if size == NARROW else "window.scrollTo(0, 0)")
                        await asyncio.sleep(3.0)
                        runs.append(await perf_trace(run, cdp, f"{vlabel}-{condition}", condition, size,
                                                     traffic is not None))
                        if condition == "log-paused":
                            await perf_click(cdp, "#btn-pause", PAUSED, False)
                        elif condition == "graphs-hidden":
                            await perf_click(cdp, "#btn-graphs-toggle", GRAPHS_HIDDEN, False)
                        elif condition.startswith("log-cleared"):
                            # Undo the clear: with no traffic, a second clear empties the view and the
                            # page offers "Show cleared rows", which sets it back to everything.
                            run.stop(traffic)
                            traffic = None
                            await asyncio.sleep(1.0)
                            await perf_click(cdp, "#btn-clear", "!!document.getElementById('btn-restore')", True)
                            await perf_click(cdp, "#btn-restore", f"document.querySelectorAll('#log-body > tr.ex')"
                                             f".length === {PERF_ROWS}", True)
                        if condition.endswith("reduced-motion"):
                            await reduced_motion(cdp, False)
                        if traffic is None:
                            traffic = start_traffic(run)
                            await asyncio.sleep(5.0)
            finally:
                run.stop(traffic)
                run.stop(sim)
                out = {"chrome": version, "categories": PERF_CATEGORIES, "seconds": PERF_SECONDS,
                       "rows_cap": PERF_ROWS, "traffic_rate": TRAFFIC_RATE,
                       "profile": str(MOVING_PROFILE.relative_to(ROOT)), "runs": runs, "problems": problems}
                (run.outdir / "m3b-perf-results.json").write_text(json.dumps(out, indent=1, default=str))
                run.log(f"perf runs: {len(runs)}; console messages, exceptions and log entries: {len(problems)}")
                for problem in problems:
                    run.log(f"  {problem}")
                cdp.reader.cancel()
    run.log("done")


# ---- --m3b-perf-log: the log fix's before / after runs (Task 44) ----
# The --m3b-perf set-up (stepped demo, the log filled to its cap by a burst, then the traffic
# script at 4/s), with the log following and, during each 60 s traced run, real clicks on the
# page's own controls at fixed offsets. Run the same mode against each tree to compare (the
# page served is recorded by its SHA-256 and the path ecu_simulator imports from).
PERF_LOG_REPEATS = 2
FILTER_CHIP = 'label[for="o-no_response"]'
NO_RESPONSE_SHOWN = "document.getElementById('o-no_response').checked"
# (offset s, name, selector, the check that the page took it, value). Pause, Resume and the
# filter chip exist on both pages; Older and Jump to newest exist only on the windowed log
# (Task 43), so they are skipped, and said so, where #lognav-older is absent. Older needs a
# pinned window: a real wheel scroll up first, as a reader would.
PERF_LOG_STEPS = (
    (6.0, "pause", "#btn-pause", PAUSED, True), (10.0, "resume", "#btn-pause", PAUSED, False),
    (16.0, "filter-off", FILTER_CHIP, NO_RESPONSE_SHOWN, False),
    (20.0, "filter-on", FILTER_CHIP, NO_RESPONSE_SHOWN, True),
    (30.0, "pause", "#btn-pause", PAUSED, True), (34.0, "resume", "#btn-pause", PAUSED, False),
    (40.0, "filter-off", FILTER_CHIP, NO_RESPONSE_SHOWN, False),
    (44.0, "filter-on", FILTER_CHIP, NO_RESPONSE_SHOWN, True),
    (49.0, "older", '[data-lognav="older"]', "!document.getElementById('lognav-newer').hidden", True),
    (54.0, "jump", "#btn-follow", "document.getElementById('btn-follow').disabled", True),
)
AFTER_ONLY = {"older", "jump"}
# Event Timing, observed by this harness only (the shipped page has no observer): every 'event'
# entry at Chrome's minimum threshold, 16 ms. An interaction with no entry took under 16 ms.
EVENT_OBSERVER = """(() => {
  if (window.__et) window.__et.obs.disconnect();
  const et = window.__et = {entries: [], obs: null};
  const keep = list => { for (const e of list) et.entries.push({name: e.name, start: e.startTime, duration: e.duration,
    ps: e.processingStart, pe: e.processingEnd, id: e.interactionId || 0}); };
  et.obs = new PerformanceObserver(l => keep(l.getEntries()));
  et.obs.observe({type: 'event', durationThreshold: 16});
  et.keep = keep;
  return PerformanceObserver.supportedEntryTypes.includes('event');
})()"""
EVENT_TAKE = """(() => { const et = window.__et; if (!et) return [];
  et.keep(et.obs.takeRecords()); et.obs.disconnect(); window.__et = null; return et.entries; })()"""
# Task 45's DIAGNOSTIC runs only (never the reported comparison): GUI_PERF_DIAG turns one
# animation off by a stylesheet this harness adds before the page loads; the shipped CSS is
# unchanged. A constructed sheet (adoptedStyleSheets), because the page's CSP has no inline
# styles. GUI_PERF_IDLE=1 adds one 60 s run per viewport with the traffic stopped.
PERF_DIAG_CSS = {
    "lamp-off": ".conn__lamp, .conn__lamp::before, .conn__lamp::after { animation: none !important; }",
    "flash-off": ".sig td.changed { animation: none !important; }",
    "both-off": ".conn__lamp, .conn__lamp::before, .conn__lamp::after, .sig td.changed "
                "{ animation: none !important; }",
}
PERF_DIAG_INJECT = """(() => { const add = () => { const s = new CSSStyleSheet(); s.replaceSync(%s);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, s]; window.__perfDiag = %s; };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add(); })()"""
PERF_DIAG_CHECK = """(() => { const lamp = document.querySelector('.conn__lamp');
  return {diag: window.__perfDiag || null, sheets: document.adoptedStyleSheets.length,
          conn: document.getElementById('conn').className,
          lamp_animation: lamp ? getComputedStyle(lamp).animationName : null}; })()"""


async def perf_home(cdp: DevTools, size: tuple[int, int]) -> None:
    """The view every run is measured in: as in --m3b-perf, at 390 px the graphs at the top."""
    await cdp.js("document.getElementById('graphs-panel').scrollIntoView({block: 'start'})"
                 if size == NARROW else "window.scrollTo(0, 0)")


async def perf_log_steps(cdp: DevTools, size: tuple[int, int]) -> list[dict[str, Any]]:
    """The scripted clicks of one run, each at its offset, each a real click, each with the
    page's performance.now() just before it (the Event Timing entries are matched to it)."""
    t0 = time.monotonic()
    windowed = await cdp.js("!!document.getElementById('lognav-older')")
    out: list[dict[str, Any]] = []
    for at, name, selector, check, want in PERF_LOG_STEPS:
        if name in AFTER_ONLY and not windowed:
            out.append({"name": name, "skipped": "this page has no windowed log (Older / Jump to newest)"})
            continue
        if name == "older":
            # The wheel goes where the log box is on screen (at 390 px it is below the fold).
            await asyncio.sleep(max(0.0, t0 + at - 1.8 - time.monotonic()))
            await cdp.js("document.getElementById('logwrap').scrollIntoView({block: 'center'})")
            await asyncio.sleep(0.2)
            await cdp.wheel("#logwrap", -600)
        await asyncio.sleep(max(0.0, t0 + at - time.monotonic()))
        await cdp.js(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: 'center'}})")
        await asyncio.sleep(0.3)
        mark = await cdp.js("performance.now()")
        at_s = round(time.monotonic() - t0, 2)
        await cdp.click(selector)
        try:
            await cdp.wait_for(f"({check}) === {json.dumps(want)}", 10)
            took = True
        except RuntimeError:
            took = False
        out.append({"name": name, "selector": selector, "at_s": at_s, "mark_ms": mark, "took": took})
        await perf_home(cdp, size)
    return out


def event_timing(steps: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each click's Event Timing: of the entries from its mark to the next click's, those of
    one interaction (interactionId), the longest of them, as INP takes it (of equally long
    ones, which Event Timing rounds to 8 ms, the one with the most processing). Its input delay
    (processingStart - startTime), processing (processingEnd - processingStart) and
    presentation (the rest of its duration, to the next paint)."""
    marks = [s["mark_ms"] for s in steps if "mark_ms" in s]
    for s in steps:
        if "mark_ms" not in s:
            continue
        nxt = min((m for m in marks if m > s["mark_ms"]), default=float("inf"))
        mine = [e for e in entries if s["mark_ms"] - 1 <= e["start"] < nxt and e["id"]]
        if not mine:
            s["event_timing"] = None
            s["note"] = "no Event Timing entry: under the 16 ms threshold"
            continue
        first = min(mine, key=lambda e: e["start"])["id"]
        e = max((x for x in mine if x["id"] == first), key=lambda x: (x["duration"], x["pe"] - x["ps"]))
        s["event_timing"] = {"entry": e["name"], "duration_ms": e["duration"],
                             "input_delay_ms": round(e["ps"] - e["start"], 1),
                             "processing_ms": round(e["pe"] - e["ps"], 1),
                             "presentation_ms": round(e["start"] + e["duration"] - e["pe"], 1),
                             "entries": sorted({x["name"] for x in mine if x["id"] == first})}
    return steps


async def m3b_perf_log_session(run: Run, chrome: str, profile_dir: str) -> None:
    """Task 44's runs: per viewport, PERF_LOG_REPEATS traced 60 s runs of the log following at
    its cap, with Pause / Resume, a filter change and (on the windowed log) Older / Jump to
    newest clicked during each; busy %, the renderLog timer, long tasks and Event Timing."""
    import hashlib
    import importlib.util

    sys.path.insert(0, str(ROOT / "scripts"))
    import gui_trace_breakdown

    spec = importlib.util.find_spec("ecu_simulator")
    origin = spec.origin if spec else None
    run.log(f"ecu_simulator imports from {origin}")
    diag = os.environ.get("GUI_PERF_DIAG") or None
    if diag is not None and diag not in PERF_DIAG_CSS:
        raise SystemExit(f"GUI_PERF_DIAG must be one of {sorted(PERF_DIAG_CSS)}, not {diag!r}")
    idle = os.environ.get("GUI_PERF_IDLE") == "1"
    if diag:
        run.log(f"DIAGNOSTIC run: {diag}, by a harness stylesheet ({PERF_DIAG_CSS[diag]}); not the shipped page")
    await launch_chrome(run, chrome, profile_dir)
    problems: list[str] = []
    runs: list[dict[str, Any]] = []
    served: dict[str, Any] = {}
    diag_check: dict[str, Any] | None = None
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        version = await chrome_version(http)
        run.log(f"Chrome: {version}")
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = await m3b_cdp(run, http, ws, problems, wrapper=False)
            if diag:
                await cdp.send("Page.addScriptToEvaluateOnNewDocument", source=PERF_DIAG_INJECT % (
                    json.dumps(PERF_DIAG_CSS[diag]), json.dumps(diag)))
            m = M3b(run, cdp, http, Cases(run))
            sim = await m3b_start(run, MOVING_PROFILE, "perf-simulator.log")
            traffic = None
            try:
                async with http.get(f"http://{API}/app.js") as resp:
                    text = await resp.text()
                lines = gui_trace_breakdown.lines_in(text)
                served = {"ecu_simulator_origin": origin, "app_js_sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "worktree_app_js_sha256": hashlib.sha256((ROOT / gui_trace_breakdown.APP_JS).read_bytes())
                          .hexdigest(), "app_js_lines": lines}
                run.log(f"served app.js: {served}")
                await m.open_page()
                diag_check = await cdp.js(PERF_DIAG_CHECK)
                run.log(f"animation check (live page): {diag_check}")
                await m.click_window(120)
                run.log(f"prefill: start traffic at {PERF_PREFILL_RATE}/s until the log holds {PERF_ROWS} exchanges")
                traffic = run.spawn([sys.executable, str(ROOT / "scripts" / "gui_demo_traffic.py"), "--interface",
                                     IFACE, "--rate", PERF_PREFILL_RATE], "prefill-traffic.log")
                deadline = time.monotonic() + 300
                while (await cdp.js(PERF_LOG))["retained"] < PERF_ROWS:
                    if time.monotonic() > deadline:
                        raise RuntimeError("the log did not reach its cap")
                    await asyncio.sleep(2.0)
                run.stop(traffic)
                traffic = start_traffic(run)
                await asyncio.sleep(5.0)
                run.log(f"prefilled: {await cdp.js(PERF_LOG)}")
                for vlabel, size in (("1440x900", WIDE), ("390x844", NARROW)):
                    await cdp.viewport(*size)
                    await asyncio.sleep(1.5)
                    if idle:
                        run.log("stop traffic (idle run)")
                        run.stop(traffic)
                        traffic = None
                        await perf_home(cdp, size)
                        await asyncio.sleep(5.0)
                        runs.append(await perf_trace(run, cdp, f"{vlabel}-idle", "no-traffic", size, False, lines))
                        traffic = start_traffic(run)
                        await asyncio.sleep(5.0)
                    for rep in range(1, PERF_LOG_REPEATS + 1):
                        await perf_home(cdp, size)
                        await asyncio.sleep(3.0)
                        observed = await cdp.js(EVENT_OBSERVER)
                        result = await perf_trace(run, cdp, f"{vlabel}-following-{rep}", "following-with-clicks", size,
                                                  True, lines, lambda size=size: perf_log_steps(cdp, size))
                        entries = await cdp.js(EVENT_TAKE)
                        result["event_timing_supported"] = observed
                        result["event_entries"] = len(entries)
                        result["interactions"] = event_timing(result["interactions"], entries)
                        for s in result["interactions"]:
                            et = s.get("event_timing")
                            run.log(f"  {s['name']}: " + (s.get("skipped") or s.get("note") or
                                                          f"{et['duration_ms']} ms ({et['input_delay_ms']} / "
                                                          f"{et['processing_ms']} / {et['presentation_ms']})")
                                    + ("" if s.get("took", True) else " (NOT TAKEN)"))
                        runs.append(result)
                        # Each run starts as the first did: following, not paused, every filter on.
                        if await cdp.js(PAUSED):
                            await perf_click(cdp, "#btn-pause", PAUSED, False)
                        if not await cdp.js(NO_RESPONSE_SHOWN):
                            await perf_click(cdp, FILTER_CHIP, NO_RESPONSE_SHOWN, True)
                        if not await cdp.js("document.getElementById('btn-follow').disabled"):
                            await perf_click(cdp, "#btn-follow", "document.getElementById('btn-follow').disabled", True)
            finally:
                run.stop(traffic)
                run.stop(sim)
                out = {"chrome": version, "categories": PERF_CATEGORIES, "seconds": PERF_SECONDS,
                       "rows_cap": PERF_ROWS, "traffic_rate": TRAFFIC_RATE, "served": served,
                       "diagnostic": {"off": diag, "css": PERF_DIAG_CSS[diag], "check": diag_check} if diag
                       else None, "idle_runs": idle, "animation_check": diag_check,
                       "steps": [list(s[:2]) for s in PERF_LOG_STEPS],
                       "profile": str(MOVING_PROFILE.relative_to(ROOT)), "runs": runs, "problems": problems}
                (run.outdir / "m3b-perf-log-results.json").write_text(json.dumps(out, indent=1, default=str))
                run.log(f"perf runs: {len(runs)}; console messages, exceptions and log entries: {len(problems)}")
                for problem in problems:
                    run.log(f"  {problem}")
                cdp.reader.cancel()
    run.log("done")


# ---- --m3b-log: the windowed exchange log at a full buffer (Task 46a) ----
# The stepped demo with the log filled to its 2,000-exchange cap by a burst of the traffic
# script, then the standard rate (TRAFFIC_RATE). Each width (1440 x 900, then 390 x 844) starts
# from that full buffer: the harness first sends LOG_UNIQUE OBD mode 0x0A requests (a read
# service the traffic cycle never uses, so a service filter matches only these old exchanges),
# then LOG_AFTER_UNIQUE more by a burst, so they lie older than the 200-row window. The cases
# read only the page's visible text (rows, the count line, the window's edge rows, the state
# lines, the filter options), the data-* and aria-* attributes it writes, and geometry. The only
# code in the page is LOG_INSTR, added by this harness before the page's scripts: it counts the
# log body's mutations, and its WebSocket wrapper can drop N exchange events (a real seq gap).
LOG_UNIQUE = 250
LOG_AFTER_UNIQUE = 400
LOG_BURST_RATE = "50"
LOG_WINDOW = 200                 # app.js LOG_WINDOW
LOG_STEP = 100                   # app.js LOG_STEP
# The page's own wording (app.js), checked against the served app.js before any case.
W_MATCH = " your filters"
W_BEYOND = "beyond this window"
W_HIDDEN = "hidden by filters (not a gap)"
W_GAP = "not received"
W_LEFT = "left this view."
W_REPIN = "Rows this view was showing left too, so it moved to the oldest exchanges kept."
W_NOMATCH = "No exchanges match these filters."
W_CLEARED = "No exchanges since you cleared the view."
W_CONN = "Connection lost, then resumed."
W_RESTART = "Simulator restarted."
W_ACROSS = "across a restart"
LOG_WORDING = (W_MATCH, W_BEYOND, W_HIDDEN, W_GAP, W_LEFT, W_REPIN, W_NOMATCH, W_CLEARED, W_CONN, W_RESTART,
               W_ACROSS)

LOG_INSTR = r"""(function () {
  var L = window.__lg = { drop: 0, dropped: [], add: 0, rem: 0, inRows: 0, nb: 0, focusout: 0, batches: [] };
  var Native = window.WebSocket;
  // Test only: while L.drop > 0, exchange events are kept from the page, which then sees a seq gap.
  function W(url, protocols) {
    var ws = protocols === undefined ? new Native(url) : new Native(url, protocols);
    ws.addEventListener("message", function (e) {
      if (L.drop <= 0) return;
      try {
        var m = JSON.parse(e.data);
        if (m && m.type === "exchange") { L.drop -= 1; L.dropped.push(m.seq); e.stopImmediatePropagation(); }
      } catch (x) { /* not ours to judge */ }
    });
    return ws;
  }
  W.prototype = Native.prototype;
  W.CONNECTING = 0; W.OPEN = 1; W.CLOSING = 2; W.CLOSED = 3;
  window.WebSocket = W;
  document.addEventListener("focusout", function () { L.focusout += 1; }, true);
  document.addEventListener("DOMContentLoaded", function () {
    var body = document.getElementById("log-body");
    // Row-list changes (rows added to or removed from #log-body), one batch per delivery, and any
    // other mutation inside the rows (a hidden-run count rewritten, a row's text).
    new MutationObserver(function (recs) {
      var add = 0, rem = 0, list = false;
      recs.forEach(function (r) {
        if (r.target !== body || r.type !== "childList") { L.inRows += 1; return; }
        add += r.addedNodes.length; rem += r.removedNodes.length; list = true;
      });
      if (!list) return;
      L.add += add; L.rem += rem; L.nb += 1;
      L.batches.push({ t: performance.now(), add: add, rem: rem });
      if (L.batches.length > 4000) L.batches.splice(0, 2000);
    }).observe(body, { childList: true, subtree: true, characterData: true, attributes: true });
  });
})();"""

# The log as a reader sees it, in one evaluation. Exchange rows are keyed by their seq and time
# cell (unique across a restart). `breaks` lists consecutive exchange rows whose seqs are not
# consecutive with nothing between them to say why (a gap marker, the restart marker, or a run
# the filters hide). Each marker row carries the seqs of the exchange rows around it. `vis`: the
# exchange rows in the log box, with their offset from its top (the page's own anchor rule).
LOG_PROBE = r"""(() => {
  const L = window.__lg || {};
  const w = document.getElementById('logwrap'), wr = w.getBoundingClientRect();
  const rows = [...document.getElementById('log-body').children];
  const isEx = r => r.classList.contains('ex');
  const seq = r => Number(r.querySelector('.c-seq').textContent);
  const key = r => r.querySelector('.c-seq').textContent + '|' + r.querySelector('.c-time').title;
  const nav = side => { const tr = document.getElementById('lognav-' + side);
    return tr && !tr.hidden ? tr.querySelector('.lognav__text').textContent : null; };
  const ex = [], marks = [], hidden = [], breaks = [];
  let prev = null, why = [], pending = [];
  rows.forEach(r => {
    if (isEx(r)) {
      const s = seq(r);
      if (prev !== null && s !== prev + 1 && !why.some(t => t === 'hidden' || t.startsWith('Gap: seq')
          || t.startsWith('Simulator restarted.'))) breaks.push([prev, s]);
      pending.forEach(m => { m.next = s; });
      ex.push(r); prev = s; why = []; pending = [];
    } else if (r.classList.contains('hiddenrow')) { hidden.push(r.textContent); why.push('hidden'); }
    else {
      const m = {text: r.textContent, prev: prev, next: null};
      marks.push(m); pending.push(m); why.push(r.textContent);
    }
  });
  const vis = [];
  ex.forEach(r => { const b = r.getBoundingClientRect();
    if (b.bottom > wr.top && b.top < wr.bottom) vis.push([key(r), Math.round((b.top - wr.top) * 10) / 10]); });
  const last = ex.length ? ex[ex.length - 1].getBoundingClientRect() : null;
  const tr = document.getElementById('log-trimmed'), st = document.getElementById('log-state');
  const fb = document.getElementById('btn-follow'), pb = document.getElementById('btn-pause');
  const a = document.activeElement;
  return {count: document.getElementById('log-count').textContent, ex: ex.length, rows: rows.length,
    keys: ex.map(key), seqs: ex.map(seq), req: ex.map(r => (r.querySelector('.c-req code') || {}).textContent || ''),
    marks: marks, hidden: hidden, breaks: breaks, vis: vis,
    newestVisible: !!last && last.top >= wr.top - 0.5 && last.bottom <= wr.bottom + 0.5,
    boxOnScreen: wr.bottom > 0 && wr.top < window.innerHeight,
    trimmed: tr && !tr.hidden ? tr.textContent : null, older: nav('older'), newer: nav('newer'),
    newerButton: !!document.querySelector('#lognav-newer:not([hidden]) [data-lognav="newer"]:not([hidden])'),
    follow: {text: fb.textContent, disabled: fb.disabled},
    state: st.hidden ? null : st.textContent,
    retained: Number((/\((\d+)\)/.exec(document.getElementById('f-ecu').options[0].textContent) || [0, -1])[1]),
    paused: pb.getAttribute('aria-pressed') === 'true', pauseText: pb.textContent,
    active: a ? (a.id || a.getAttribute('data-lognav') || a.getAttribute('data-expand') || a.tagName) : null,
    scrollTop: Math.round(w.scrollTop), atBottom: w.scrollHeight - w.scrollTop - w.clientHeight < 4,
    conn: document.getElementById('conn-text').textContent,
    m: {add: L.add, rem: L.rem, inRows: L.inRows, nb: L.nb, focusout: L.focusout}};
})()"""

# OVERFLOW plus the log's own boxes.
LOG_OVERFLOW = ("(() => { const out = " + OVERFLOW.strip() + """;
  ['log-panel', 'logwrap', 'log-state'].forEach(id => { const e = document.getElementById(id);
    if (e && !e.hidden) out.push(['#' + id, e.scrollWidth, e.clientWidth]); });
  return out;
})()""")


def n_of(text: str) -> int:
    return int(text.replace(",", ""))


def fmt_n(n: int) -> str:
    return f"{n:,}"


def last_seq(p: dict[str, Any]) -> int | None:
    m = re.search(r"last seq (\d+)", p["count"])
    return int(m.group(1)) if m else None


def edge_count(text: str | None, side: str) -> int:
    """The matching exchanges an Older / Newer row counts (0 when the row is absent)."""
    m = re.match(rf"([\d,]+) {side} exchanges? match", text or "")
    return n_of(m.group(1)) if m else 0


def beyond_count(p: dict[str, Any]) -> int:
    m = re.search(r"([\d,]+) (?:rows? )?beyond this window", p["follow"]["text"])
    return n_of(m.group(1)) if m else 0


def held_count(p: dict[str, Any]) -> int | None:
    m = re.search(r"([\d,]+) new exchanges? (?:are|is) held", p["state"] or "")
    return n_of(m.group(1)) if m else None


def trimmed_count(p: dict[str, Any]) -> int | None:
    m = re.match(r"([\d,]+) older rows?", p["trimmed"] or "")
    return n_of(m.group(1)) if m else None


def accounting(p: dict[str, Any]) -> dict[str, Any]:
    """The count line, the Older / Newer rows and the drawn rows, read as text: matching =
    older + shown + newer, and the count line's shown = the exchange rows drawn."""
    m = re.match(r"([\d,]+) of ([\d,]+) (?:matching )?shown", p["count"])
    one = re.match(r"([\d,]+) exchanges?\b", p["count"])
    if m:
        shown, of = n_of(m.group(1)), n_of(m.group(2))
    elif one:
        shown = of = n_of(one.group(1))
    else:
        return {"count": p["count"], "adds_up": False, "why": "count line not read"}
    older, newer = edge_count(p["older"], "older"), edge_count(p["newer"], "newer")
    return {"count": p["count"], "matching": of, "older": older, "shown": shown, "newer": newer, "rows": p["ex"],
            "adds_up": of == older + shown + newer and shown == p["ex"]}


def delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, int]:
    return {k: b["m"][k] - a["m"][k] for k in a["m"]}


def window_of(p: dict[str, Any]) -> list[Any]:
    return [p["seqs"][0], p["seqs"][-1], p["ex"]] if p["seqs"] else [None, None, 0]


class LogRun:
    """One page, the simulator and the traffic for --m3b-log, and the helpers every case uses."""

    def __init__(self, run: Run, m: M3b, sim: subprocess.Popen[bytes]) -> None:
        self.run, self.m, self.cdp, self.sim = run, m, m.cdp, sim
        self.traffic: subprocess.Popen[bytes] | None = None
        self.label = ""
        self.size = WIDE
        self.sims = 1
        # Per width: texts seen for each absent-row kind, each accounting reading, each overflow reading.
        self.seen: dict[str, list[str]] = {}
        self.accounts: list[dict[str, Any]] = []
        self.overflows: list[dict[str, Any]] = []

    async def probe(self) -> dict[str, Any]:
        result: dict[str, Any] = await self.cdp.js(LOG_PROBE)
        return result

    async def settle(self, extra: float = 0.3) -> None:
        await self.m.settled()
        await asyncio.sleep(extra)

    async def now_ms(self) -> float:
        return float(await self.cdp.js("performance.now()"))

    async def batches_since(self, t: float) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self.cdp.js(f"window.__lg.batches.filter(b => b.t >= {t})")
        return result

    def steady(self) -> None:
        if self.traffic is None:
            self.traffic = start_traffic(self.run)

    def quiet(self) -> None:
        if self.traffic is not None:
            self.run.log("stop traffic")
            self.run.stop(self.traffic)
            self.traffic = None

    def _burst(self) -> subprocess.Popen[bytes]:
        self.quiet()
        self.run.log(f"burst: traffic at {LOG_BURST_RATE}/s")
        return self.run.spawn([sys.executable, str(ROOT / "scripts" / "gui_demo_traffic.py"), "--interface", IFACE,
                               "--rate", LOG_BURST_RATE, "--timeout", "0.1"], "burst-traffic.log")

    async def burst(self, n: int, until: Callable[[dict[str, Any]], bool] | None = None,
                    timeout: float = 200.0) -> list[dict[str, Any]]:
        """A burst of at least n exchanges (by the count line's last seq), or until ``until``
        holds; the probes taken meanwhile (one a second) are returned."""
        start = last_seq(await self.probe()) or 0
        proc = self._burst()
        samples: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(1.0)
                p = await self.probe()
                samples.append(p)
                if until is not None:
                    if until(p):
                        break
                elif (last_seq(p) or 0) - start >= n:
                    break
            else:
                raise RuntimeError(f"burst: the condition did not hold in {timeout} s")
        finally:
            self.run.stop(proc)
        await asyncio.sleep(0.6)
        return samples

    async def unique(self, n: int) -> None:
        """n OBD mode 0x0A requests from this harness (the simulator answers each with a negative
        response), with the traffic stopped; then wait until the page has received them all."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import gui_demo_traffic

        def send() -> None:
            tester = gui_demo_traffic.Tester(IFACE, 0.05)
            try:
                for _ in range(n):
                    tester.exchange(gui_demo_traffic.FUNCTIONAL, b"\x0a")
            finally:
                tester.close()

        self.quiet()
        await asyncio.sleep(1.0)
        start = last_seq(await self.probe()) or 0
        self.run.log(f"send {n} OBD 0x0A requests from the harness, after seq {start}")
        await asyncio.to_thread(send)
        deadline = time.monotonic() + 30
        while (last_seq(await self.probe()) or 0) - start < n:
            if time.monotonic() > deadline:
                raise RuntimeError(f"the page did not receive the {n} 0x0A exchanges")
            await asyncio.sleep(0.5)

    async def page_at(self, selector: str) -> None:
        """Bring a control outside the log box into the viewport (the page scrolls, not the box)."""
        await self.cdp.js(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: 'center'}})")
        await asyncio.sleep(0.3)

    async def click(self, selector: str) -> None:
        """A real click on a control outside the log box."""
        await self.page_at(selector)
        await self.cdp.click(selector)
        await self.settle()

    async def box_at(self, selector: str, block: str = "center") -> None:
        """Bring a control inside the log box into view. The page takes a box scroll within
        USER_SCROLL_MS (1 s) of the reader's own input as theirs; this waits that out, so the
        scroll is the harness's, as a layout scroll would be."""
        await asyncio.sleep(1.2)
        await self.cdp.js(f"document.querySelector({json.dumps(selector)}).scrollIntoView({{block: '{block}'}})")
        await asyncio.sleep(0.6)

    async def focus(self, selector: str) -> None:
        await self.cdp.js(f"document.querySelector({json.dumps(selector)}).focus({{preventScroll: true}})")

    async def key_enter(self) -> None:
        for kind in ("keyDown", "keyUp"):
            await self.cdp.send("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter",
                                windowsVirtualKeyCode=13, **({"text": "\r"} if kind == "keyDown" else {}))

    async def wheel_up(self, dy: float = -600) -> None:
        """A real wheel scroll up over the log box (which pins the window)."""
        await self.cdp.js("document.getElementById('logwrap').scrollIntoView({block: 'center'})")
        await asyncio.sleep(0.3)
        await self.cdp.wheel("#logwrap", dy)
        await asyncio.sleep(1.2)

    async def jump(self) -> None:
        """Jump to newest by the header control, a real click; nothing when already following."""
        if not (await self.probe())["follow"]["disabled"]:
            await self.click("#btn-follow")
            await asyncio.sleep(0.5)

    async def select(self, sel_id: str, value: str) -> None:
        """A <select> change as the page receives it (a native popup is not driven over CDP)."""
        await self.cdp.js(f"(() => {{ const s = document.getElementById({json.dumps(sel_id)}); s.value = "
                          f"{json.dumps(value)}; s.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
        await self.settle()

    def note(self, kind: str, text: str | None) -> None:
        if text:
            self.seen.setdefault(kind, []).append(text)

    def account(self, where: str, p: dict[str, Any]) -> dict[str, Any]:
        a = {"where": where, **accounting(p)}
        self.accounts.append(a)
        return a

    async def overflow(self, state: str) -> None:
        measured = await self.cdp.js(LOG_OVERFLOW)
        bad = [f"{n} {s} > {c}" for n, s, c in measured if s > c]
        self.overflows.append({"state": state, "measured": measured, "over": bad})
        await check_overflow(self.run, self.cdp, f"M3b log {self.label} {state}")

    def record(self, n: int, name: str, rule: str, conds: dict[str, bool], observed: dict[str, Any]) -> None:
        self.m.cases.record(f"{self.label}: {n}. {name}", rule, conds, {"width": self.label, **observed})

    async def nav(self, kind: str, how: str) -> dict[str, Any]:
        """One press of Older / Newer / the in-row Jump to newest: a real click (the control
        brought into view first) or Enter on the focused control (no scroll at all). The rows
        in the log box are read just before the press; the anchor is the first of them still
        drawn after it, as the page's own rule takes it."""
        selector = f'[data-lognav="{kind}"]'
        if how == "click":
            await self.box_at(selector, "center" if kind == "older" else "end")
            a = await self.probe()
            await self.cdp.click(selector)
        else:
            await self.focus(selector)
            a = await self.probe()
            await self.key_enter()
        await self.settle()
        b = await self.probe()
        after = dict(b["vis"])
        anchor = next((k for k, _ in a["vis"] if k in set(b["keys"])), None)
        off0 = dict(a["vis"]).get(anchor) if anchor else None
        off1 = after.get(anchor) if anchor else None
        moved = a["keys"] != b["keys"]
        clamp = b["scrollTop"] == 0 or b["atBottom"]
        loaded = None
        if moved and a["keys"] and b["keys"]:
            if kind == "older" and a["keys"][0] in b["keys"]:
                loaded = b["keys"].index(a["keys"][0])
            elif kind != "older" and b["keys"][0] in a["keys"]:
                loaded = a["keys"].index(b["keys"][0])
        step = {"kind": kind, "how": how, "before": window_of(a), "after": window_of(b), "moved": moved,
                "loaded": loaded, "scroll": [a["scrollTop"], b["scrollTop"]],
                "to_end": b["scrollTop"] == 0 if kind == "older" else b["atBottom"],
                "anchor": anchor, "offset": [off0, off1],
                "diff": round(off1 - off0, 1) if off0 is not None and off1 is not None else None,
                "clamp": clamp, "following": b["follow"]["disabled"], "focus": b["active"],
                "overlap": bool(set(a["keys"]) & set(b["keys"])), "breaks": b["breaks"], "ex": b["ex"],
                "older": b["older"], "newer": b["newer"]}
        # The rule for one press: it moves the window, or (a step of 0) scrolls the box to the
        # window's other end; focus to the log box; rows contiguous; a moved window overlaps the
        # last one; the anchor keeps its offset within 1 px unless the box is at a scroll limit,
        # or the press reached the newest (following shows the newest at the bottom).
        step["ok"] = ((moved or step["to_end"]) and b["active"] == "logwrap" and not b["breaks"]
                      and (not moved or step["overlap"] or b["follow"]["disabled"])
                      and (not moved or b["follow"]["disabled"] or clamp
                           or (step["diff"] is not None and abs(step["diff"]) <= 1.0)))
        step["keys"] = b["keys"]
        return step


async def case_log_following(lg: LogRun) -> None:
    lg.steady()
    await lg.jump()
    await asyncio.sleep(2.0)
    p0 = await lg.probe()
    samples = [p0]
    for _ in range(10):
        await asyncio.sleep(1.0)
        samples.append(await lg.probe())
    p1 = samples[-1]
    d = delta(p0, p1)
    arrived = (last_seq(p1) or 0) - (last_seq(p0) or 0)
    lg.note("outside", p1["older"])
    acc = lg.account("following", p1)
    await lg.overflow("following")
    # No new matching exchange: the traffic stopped, then a filter no arrival matches.
    lg.quiet()
    await asyncio.sleep(1.5)
    q0 = await lg.probe()
    await asyncio.sleep(5.0)
    q1 = await lg.probe()
    dq = delta(q0, q1)
    await lg.select("f-service", "0a")
    lg.steady()
    await asyncio.sleep(2.0)
    f0 = await lg.probe()
    await asyncio.sleep(8.0)
    f1 = await lg.probe()
    df = delta(f0, f1)
    f_arrived = (last_seq(f1) or 0) - (last_seq(f0) or 0)
    await lg.select("f-service", "all")
    await asyncio.sleep(1.0)
    lg.record(
        1, "Live following",
        "Full buffer (2,000 retained). Following at the standard rate for 10 s: the window slides (its newest seq "
        "rises); at every sample <= 200 exchange rows and the body holds only them plus marker / hidden rows; the "
        "newest exchange is drawn in the log box and is the count line's last seq; one row in and one out per "
        "arrival (row-list adds = removes = arrivals, no other row mutation). No new matching exchange: 5 s "
        "with the traffic stopped, zero mutations of any kind; 8 s of arrivals under a filter none of them match "
        "(service 0x0A), zero row-list adds / removes and the same rows",
        {"full buffer at the start": p0["retained"] == PERF_ROWS,
         "following throughout (the jump control off)": all(s["follow"]["disabled"] for s in samples),
         "the window slides": bool(p0["seqs"]) and bool(p1["seqs"]) and p1["seqs"][-1] > p0["seqs"][-1],
         "<= 200 exchange rows at every sample, 200 at the cap": all(s["ex"] == LOG_WINDOW for s in samples),
         "the body holds only exchange, marker and hidden rows": all(
             s["rows"] == s["ex"] + len(s["marks"]) + len(s["hidden"]) for s in samples),
         "the newest exchange drawn, in the log box, at every sample": all(
             s["newestVisible"] and s["boxOnScreen"] and s["seqs"][-1] == last_seq(s) for s in samples),
         "one in / one out per arrival": arrived >= 20 and d["add"] == arrived and d["rem"] == arrived
         and d["inRows"] == 0 and d["nb"] >= 1,
         "no arrival: zero mutations in 5 s": all(v == 0 for k, v in dq.items() if k != "focusout"),
         "arrivals no filter matches: zero row-list mutations, same rows": f_arrived >= 15 and df["add"] == 0
         and df["rem"] == 0 and f0["keys"] == f1["keys"],
         "counts add up": acc["adds_up"]},
        {"retained": p0["retained"], "windows": [window_of(s) for s in samples],
         "last_seq": [last_seq(s) for s in samples], "arrived": arrived, "mutations": d,
         "count": p1["count"], "older_row": p1["older"], "rows_body": p1["rows"],
         "quiet_mutations": dq, "filtered": {"arrived": f_arrived, "mutations": df, "count": f1["count"],
                                             "hidden_rows": f1["hidden"], "window": window_of(f1)},
         "accounting": acc})


LOG_TEXT = "document.getElementById('log-body').innerText"


async def case_log_pause(lg: LogRun) -> None:
    lg.steady()
    await lg.jump()
    await asyncio.sleep(1.5)
    p_start = await lg.probe()
    await lg.click("#btn-pause")
    await asyncio.sleep(0.5)
    a = await lg.probe()
    text_a = await lg.cdp.js(LOG_TEXT)
    held = [held_count(a)]
    for _ in range(4):
        await asyncio.sleep(5.0)
        held.append(held_count(await lg.probe()))
    b = await lg.probe()
    text_b = await lg.cdp.js(LOG_TEXT)
    d = delta(a, b)
    paused_last = a["seqs"][-1]
    h = held_count(b) or 0
    t = await lg.now_ms()
    await lg.click("#btn-pause")
    await asyncio.sleep(1.0)
    c = await lg.probe()
    batches = await lg.batches_since(t)
    first = batches[0] if batches else {"add": 0, "rem": 0}
    want = set(range(paused_last + 1, paused_last + h + 1))
    lg.note("held", b["state"])
    # Clear view keeps its meaning: rows up to now leave this view, the page keeps them (retained),
    # new ones appear; paused and cleared, Show cleared rows brings the window back.
    l0 = last_seq(c) or 0
    await lg.click("#btn-clear")
    k0 = await lg.probe()
    await asyncio.sleep(3.0)
    k1 = await lg.probe()
    await lg.click("#btn-pause")
    await lg.click("#btn-clear")
    k2 = await lg.probe()
    await lg.click("#btn-restore")
    k3 = await lg.probe()
    await lg.click("#btn-pause")
    await asyncio.sleep(1.0)
    k4 = await lg.probe()
    lg.record(
        2, "Pause / resume, Clear view",
        "Full buffer, standard traffic. Pause view: aria-pressed true, the button reads Resume view. Paused 20 s: "
        "zero row-list and in-row mutations, the same rows and the same log text, the held counter rising at every "
        "5 s sample. Resume: the first row-list batch brings every held exchange at once (one rebuild: its adds >= "
        "the held count), later batches are single arrivals; every held seq is drawn; following, newest drawn. "
        "Clear view: no row up to the clear is drawn, the page still retains 2,000, new exchanges appear; paused "
        "and cleared: No exchanges since you cleared the view. with Show cleared rows, which brings back the "
        "200-row window; resume follows again",
        {"full buffer at the start": p_start["retained"] == PERF_ROWS,
         "paused: aria-pressed true, Resume view": a["paused"] and a["pauseText"] == "Resume view",
         "paused 20 s: zero mutations": d["add"] == d["rem"] == d["nb"] == d["inRows"] == 0,
         "paused: same rows and the log text frozen": a["keys"] == b["keys"] and text_a == text_b,
         "paused: the held counter rises at every sample": None not in held
         and all((y or 0) > (x or 0) for x, y in itertools.pairwise(held)) and h >= 40,
         "paused: the held count is the arrivals since the pause": (last_seq(b) or 0) - paused_last == h,
         "resume: one rebuild brings the held rows": first["add"] >= h and first["rem"] == first["add"]
         and all(x["add"] <= 3 for x in batches[1:]),
         "resume: every held seq drawn, following, newest drawn": want <= set(c["seqs"]) and not c["paused"]
         and c["pauseText"] == "Pause view" and c["follow"]["disabled"] and c["newestVisible"] and c["ex"] == 200,
         "Clear view: no row up to the clear drawn, 2,000 retained": all(s > l0 for s in k0["seqs"] + k1["seqs"])
         and k0["retained"] == k1["retained"] == PERF_ROWS,
         "Clear view: new exchanges appear, following": k1["ex"] > 0 and k1["follow"]["disabled"]
         and "2,000 retained" in k1["count"],
         "paused and cleared: the cleared line with Show cleared rows": k2["ex"] == 0
         and (k2["state"] or "").find(W_CLEARED) >= 0 and "Show cleared rows" in (k2["state"] or ""),
         "Show cleared rows: the 200-row window back": k3["ex"] == 200 and k3["paused"],
         "resume after: following, 200 rows, newest drawn": k4["ex"] == 200 and k4["follow"]["disabled"]
         and not k4["paused"] and k4["newestVisible"]},
        {"retained": p_start["retained"], "paused_window": window_of(a), "held": held, "mutations_paused": d,
         "state_paused": b["state"], "text_frozen": text_a == text_b, "resume_batches": batches[:6],
         "resumed_window": window_of(c), "held_seqs": [min(want, default=None), max(want, default=None)],
         "clear": {"last_seq_at_clear": l0, "just_after": [window_of(k0), k0["count"], k0["state"]],
                   "3s_after": [window_of(k1), k1["count"]], "paused_cleared": [k2["ex"], k2["state"]],
                   "restored": [window_of(k3), k3["count"]], "resumed": [window_of(k4), k4["count"]]}})


async def case_log_filters(lg: LogRun) -> None:
    lg.steady()
    await lg.jump()
    await asyncio.sleep(1.0)
    lg.quiet()                                  # rebuild counts: the filter's own render only
    await asyncio.sleep(1.5)
    u = await lg.probe()
    option = await lg.cdp.js("document.querySelector('#f-service option[value=\"0a\"]').textContent")
    obs: dict[str, Any] = {"retained": u["retained"], "unfiltered_window": window_of(u), "option": option}
    conds: dict[str, bool] = {"full buffer at the start": u["retained"] == PERF_ROWS,
                              f"the 0x0A option counts {LOG_UNIQUE}": option == f"0x0A ({LOG_UNIQUE})"}
    # A filter matching only exchanges older than the window.
    await lg.select("f-service", "0a")
    f = await lg.probe()
    df = delta(u, f)
    acc_f = lg.account("service 0x0A, following", f)
    lg.note("outside", f["older"])
    for x in f["hidden"]:
        lg.note("hidden", x)
    count_re = rf"{LOG_WINDOW} of {fmt_n(LOG_UNIQUE)} matching shown \(seq [\d,]+–[\d,]+; 2,000 retained\)"
    conds.update({
        "0x0A: one rebuild": df["nb"] == 1,
        "0x0A: the count line": re.match(count_re, f["count"]) is not None,
        "0x0A: every drawn row is a 0x0A request": f["ex"] == LOG_WINDOW
        and all(r.upper().startswith("0A") for r in f["req"]),
        "0x0A: all older than the unfiltered window": bool(f["seqs"]) and max(f["seqs"]) < u["seqs"][0],
        "0x0A: the Older row counts the rest, and the hidden ones as not a gap": re.match(
            rf"{LOG_UNIQUE - LOG_WINDOW} older exchanges match your filters; (?:[^;]+ in older rows; )?[\d,]+ "
            rf"older exchanges {re.escape(W_HIDDEN)}\.$", f["older"] or "") is not None,
        "0x0A: the trailing hidden run worded (not a gap)": len(f["hidden"]) == 1
        and re.fullmatch(rf"[\d,]+ exchanges {re.escape(W_HIDDEN)}", f["hidden"][0]) is not None,
        "0x0A: counts add up": acc_f["adds_up"]})
    await lg.overflow("filtered, following")
    # The Older route reaches the rest.
    o = await lg.nav("older", "click")
    p = await lg.probe()
    acc_o = lg.account("service 0x0A, after Older", p)
    reached = set(f["keys"]) | set(p["keys"])
    lg.note("hidden", p["hidden"][0] if p["hidden"] else None)
    conds.update({
        "Older: one press, a correct step": o["ok"],
        "Older: every 0x0A exchange reached": len(reached) == LOG_UNIQUE and p["older"] is None,
        "Older: the Newer row counts the rest": edge_count(p["newer"], "newer") == LOG_UNIQUE - LOG_WINDOW,
        "Older: the leading hidden run worded (not a gap)": bool(p["hidden"])
        and re.fullmatch(rf"[\d,]+ exchanges {re.escape(W_HIDDEN)}", p["hidden"][0]) is not None,
        "Older: counts add up": acc_o["adds_up"]})
    obs.update({"filtered": {"count": f["count"], "window": window_of(f), "older": f["older"], "hidden": f["hidden"],
                             "mutations": df, "accounting": acc_f},
                "older_press": {k: v for k, v in o.items() if k != "keys"},
                "after_older": {"count": p["count"], "window": window_of(p), "newer": p["newer"],
                                "hidden": p["hidden"], "reached": len(reached), "accounting": acc_o}})
    await lg.jump()
    # Back to all services, then outcome chips off one by one until nothing matches.
    s0 = await lg.probe()
    await lg.select("f-service", "all")
    s1 = await lg.probe()
    changes = [{"filter": "service all", "rebuilds": delta(s0, s1)["nb"], "rows_changed": s0["keys"] != s1["keys"]}]
    chips = [i for i, on in await lg.cdp.js("[...document.querySelectorAll('#f-outcome input')]"
                                              ".map(i => [i.id, i.checked])") if on]
    order = ["o-no_response"] + [c for c in chips if c != "o-no_response"]
    between: dict[str, Any] = {}
    last = s1
    for chip in order:
        await lg.click(f'label[for="{chip}"]')
        q = await lg.probe()
        changes.append({"filter": f"{chip} off", "rebuilds": delta(last, q)["nb"],
                        "rows_changed": last["keys"] != q["keys"], "count": q["count"], "hidden": q["hidden"][:3]})
        if chip == "o-no_response":
            between = {"count": q["count"], "hidden": q["hidden"][:4], "older": q["older"],
                       "accounting": lg.account("no response off", q)}
            for x in q["hidden"][:2]:
                lg.note("hidden", x)
        last = q
    nothing = last
    await lg.overflow("nothing matches")
    conds.update({
        # A change that redraws no exchange row may still move a hidden-run row (one rebuild) or
        # only rewrite its count (none): at most one, and exactly one when the exchange rows changed.
        "every filter change: at most one rebuild, one when the exchange rows changed": all(
            c["rebuilds"] <= 1 and (c["rebuilds"] == 1 or not c["rows_changed"]) for c in changes),
        "no response off: hidden runs between rows, worded (not a gap)": bool(between.get("hidden"))
        and all(re.fullmatch(rf"[\d,]+ exchanges? {re.escape(W_HIDDEN)}", x) for x in between["hidden"])
        and between["accounting"]["adds_up"],
        "nothing matches: no row, the no-match line with Reset filters": nothing["ex"] == 0
        and (nothing["state"] or "").startswith(W_NOMATCH) and "Reset filters" in (nothing["state"] or "")})
    # Reset filters keeps the focus while arrivals update the counts, then still works.
    lg.steady()
    await lg.focus("#btn-reset")
    r0 = await lg.probe()
    await asyncio.sleep(6.0)
    r1 = await lg.probe()
    await lg.click("#btn-reset")
    await asyncio.sleep(0.5)
    r2 = await lg.probe()
    checked = await lg.cdp.js("[...document.querySelectorAll('#f-outcome input')].every(i => i.checked)")
    conds.update({
        "a focused Reset filters keeps the focus through 6 s of arrivals": r0["active"] == "btn-reset"
        and r1["active"] == "btn-reset" and delta(r0, r1)["focusout"] == 0
        and (last_seq(r1) or 0) > (last_seq(r0) or 0),
        "Reset filters: every filter back, 200 rows, following, focus on the ECU filter": r2["ex"] == LOG_WINDOW
        and checked and r2["follow"]["disabled"] and r2["active"] == "f-ecu"})
    obs.update({"changes": changes, "no_response_off": between,
                "nothing": {"count": nothing["count"], "state": nothing["state"]},
                "reset": {"focus": [r0["active"], r1["active"]], "focusout": delta(r0, r1)["focusout"],
                          "arrived": (last_seq(r1) or 0) - (last_seq(r0) or 0), "after": [r2["count"], r2["active"]]}})
    lg.record(
        3, "Filtering across all retained exchanges",
        f"Full buffer; traffic stopped while filters change (so every rebuild is the filter's own). Service 0x0A, "
        f"which only the harness's {LOG_UNIQUE} old requests match: one rebuild; the count line reads "
        f"'{LOG_WINDOW} of {LOG_UNIQUE} matching shown (seq a–b; 2,000 retained)'; every drawn row is a 0x0A "
        "request and older than the unfiltered window; the Older row counts the other 50 (then any older "
        "markers) and the hidden older ones as not a gap; Older (a real click) reaches all of them, the leading "
        "hidden run worded (not a gap). Then all services, and the outcome chips off one at a time (no response "
        "first: hidden runs between rows worded (not a gap)) until nothing matches: No exchanges match these "
        "filters. with Reset filters. Each change: at most one rebuild, and one whenever the drawn exchange rows "
        "changed. A focused Reset filters keeps the focus through 6 s of arrivals; pressing it restores every "
        "filter, 200 rows, following, focus on the ECU filter. Counts add up in every filtered state",
        conds, obs)


def walk_summary(steps: list[dict[str, Any]], start: list[str]) -> dict[str, Any]:
    union = set(start)
    for s in steps:
        union |= set(s["keys"])
    return {"presses": len(steps), "moved": sum(1 for s in steps if s["moved"]),
            "zero_steps_scrolled": sum(1 for s in steps if not s["moved"] and s["to_end"]),
            "clamps": sum(1 for s in steps if s["moved"] and s["clamp"]
                          and (s["diff"] is None or abs(s["diff"]) > 1.0)),
            "silent": sum(1 for s in steps if not s["moved"] and not s["to_end"]),
            "bad": [{k: v for k, v in s.items() if k != "keys"} for s in steps if not s["ok"]],
            "loaded": [s["loaded"] for s in steps], "max_anchor_diff_unclamped": max(
                (abs(s["diff"]) for s in steps if s["moved"] and not s["clamp"] and s["diff"] is not None),
                default=None), "union": len(union)}


async def case_log_navigation(lg: LogRun) -> None:
    lg.steady()
    await lg.jump()
    await asyncio.sleep(2.0)
    s0 = await lg.probe()
    # a. A reader's scroll up pins: the rows stay put while arrivals are counted beyond the window.
    await lg.wheel_up()
    p0 = await lg.probe()
    beyond = [beyond_count(p0)]
    for _ in range(4):
        await asyncio.sleep(2.0)
        beyond.append(beyond_count(await lg.probe()))
    p1 = await lg.probe()
    d = delta(p0, p1)
    off1 = dict(p1["vis"])
    still = bool(p0["vis"]) and all(k in off1 and abs(off1[k] - v) <= 1.0 for k, v in p0["vis"])
    lg.note("outside", p1["newer"])
    lg.note("outside", p1["follow"]["text"])
    acc_p = lg.account("pinned by a scroll", p1)
    await lg.overflow("pinned, Newer row shown")
    # b-d. Older, Newer, Jump to newest: real clicks.
    older = await lg.nav("older", "click")
    newer = await lg.nav("newer", "click")
    j0 = await lg.probe()
    await lg.click("#btn-follow")
    await asyncio.sleep(0.5)
    j1 = await lg.probe()
    await asyncio.sleep(2.5)
    j2 = await lg.probe()
    # e. The whole history: traffic stopped, pinned, Older by Enter (no scroll) until no older
    # exchange is left, then Newer by Enter until following again.
    lg.quiet()
    await asyncio.sleep(1.5)
    await lg.wheel_up(-300)
    w0 = await lg.probe()
    back: list[dict[str, Any]] = []
    for _ in range(80):
        if (await lg.probe())["older"] is None:
            break
        back.append(await lg.nav("older", "enter"))
    oldest = await lg.probe()
    fwd: list[dict[str, Any]] = []
    for _ in range(80):
        if (await lg.probe())["follow"]["disabled"]:
            break
        fwd.append(await lg.nav("newer", "enter"))
    end = await lg.probe()
    sb, sf = walk_summary(back, w0["keys"]), walk_summary(fwd, oldest["keys"])
    lg.steady()
    lg.record(
        4, "Older-history navigation",
        "Full buffer. A real wheel scroll up pins: for 8 s of arrivals the drawn rows are unchanged and every visible"
        " row keeps its offset within 1 px, zero row-list mutations, the header's 'beyond this window' count rises "
        "and equals the Newer row's count. Older and Newer (real clicks) each move the window by 100 with the anchor "
        "row within 1 px and focus on the log box; Jump to newest (the header control, a real click) follows again: "
        "newest drawn, at the bottom, the control off, the window sliding. With the traffic stopped, Older pressed by"
        " Enter with no scroll until no older exchange matches, then Newer until following: every press moves the "
        "window or (a step of 0) scrolls the box to the window's other end, never nothing; focus on the log box; rows"
        " contiguous; each moved window overlaps the last; the anchor within 1 px except at a scroll limit (a clamp, "
        "counted); the windows together hold every retained exchange (2,000), both ways",
        {"full buffer at the start": s0["retained"] == PERF_ROWS,
         "scroll up pins (the jump control on)": not p0["follow"]["disabled"] and not p1["follow"]["disabled"],
         "pinned: the same rows, every visible row within 1 px of its offset": p0["keys"] == p1["keys"] and still,
         "pinned: zero row-list mutations": d["add"] == d["rem"] == d["nb"] == 0,
         "pinned: arrivals counted beyond this window, rising": all(y > x for x, y in itertools.pairwise(beyond))
         and (last_seq(p1) or 0) > (last_seq(p0) or 0),
         "pinned: the Newer row counts the same": edge_count(p1["newer"], "newer") == beyond_count(p1),
         "pinned: counts add up": acc_p["adds_up"],
         "Older (click): 100 back, anchor kept, focus on the log box": older["ok"] and older["loaded"] == LOG_STEP
         and not older["clamp"],
         "Newer (click): 100 on, anchor kept, focus on the log box": newer["ok"] and newer["loaded"] == LOG_STEP
         and not newer["clamp"],
         "Jump to newest: following, newest drawn, at the bottom": not j0["follow"]["disabled"]
         and j1["follow"]["disabled"] and j1["atBottom"] and j1["newestVisible"] and j1["seqs"][-1] == last_seq(j1),
         "Jump to newest: the window slides again": j2["seqs"][-1] > j1["seqs"][-1] and j2["ex"] == LOG_WINDOW,
         "walk back: every press correct, none silent": bool(back) and not sb["bad"] and sb["silent"] == 0,
         "walk back: reached the oldest (no Older row) and every retained exchange": oldest["older"] is None
         and sb["union"] == oldest["retained"] == PERF_ROWS,
         "walk back: at least one step of 0 that scrolled to the top": sb["zero_steps_scrolled"] >= 1,
         "walk forward: every press correct, none silent": bool(fwd) and not sf["bad"] and sf["silent"] == 0,
         "walk forward: following again, every retained exchange": end["follow"]["disabled"]
         and sf["union"] == PERF_ROWS and end["newestVisible"]},
        {"retained": s0["retained"], "pinned": {"window": window_of(p0), "beyond": beyond, "mutations": d,
                                                "newer": p1["newer"], "follow": p1["follow"]["text"],
                                                "anchor": p0["vis"][:1], "accounting": acc_p},
         "older_click": {k: v for k, v in older.items() if k != "keys"},
         "newer_click": {k: v for k, v in newer.items() if k != "keys"},
         "jump": {"before": j0["follow"]["text"], "after": [window_of(j1), j1["atBottom"]], "later": window_of(j2)},
         "walk_back": sb, "walk_forward": sf, "oldest_window": window_of(oldest),
         "oldest_count": oldest["count"], "back_steps": [{k: s[k] for k in ("before", "after", "loaded", "scroll",
                                                                                "diff", "clamp", "focus")}
                                                         for s in back],
         "forward_steps": [{k: s[k] for k in ("before", "after", "loaded", "scroll", "diff", "clamp", "following")}
                           for s in fwd]})


async def case_log_expansion(lg: LogRun) -> None:
    lg.steady()
    await lg.jump()
    await asyncio.sleep(1.0)
    await lg.wheel_up()
    key = await lg.cdp.js("(() => { const bs = [...document.querySelectorAll('#log-body [data-expand]')];"
                          " return bs.length >= 3 ? bs[bs.length - 3].dataset.expand : null; })()")
    sel = f'[data-expand="{key}"]'
    state = ("(() => { const b = document.querySelector(" + json.dumps(sel) + "); if (!b) return null;"
             " const c = b.closest('td').querySelector('code');"
             " return [b.getAttribute('aria-expanded'), c.className, c.textContent.split(' ').length]; })()")
    opened = None
    if key:
        await lg.box_at(sel)
        await lg.cdp.click(sel)
        await lg.settle()
        opened = await lg.cdp.js(state)
    left = []
    for _ in range(8):
        if await lg.cdp.js(state) is None:
            break
        left.append(await lg.nav("older", "enter"))
    gone = await lg.cdp.js(state) is None
    came = []
    for _ in range(10):
        if await lg.cdp.js(state) is not None:
            break
        came.append(await lg.nav("newer", "enter"))
    back = await lg.cdp.js(state)
    await lg.jump()
    lg.record(
        7, "Payload expansion kept",
        "Pinned, with traffic: a real click on a row's 'show all' opens it (aria-expanded true, hex--open, more than "
        "6 bytes shown); Older moves the window until that row is no longer drawn; Newer brings it back, still "
        "expanded (aria-expanded true, hex--open, the same bytes)",
        {"a row to expand": key is not None,
         "opened": opened is not None and opened[0] == "true" and "hex--open" in opened[1] and opened[2] > 6,
         "the row left the window": gone and bool(left),
         "the row came back still expanded": back is not None and opened is not None and back[0] == "true"
         and "hex--open" in back[1] and back[2] == opened[2],
         "every press correct": all(s["ok"] for s in left + came)},
        {"key": key, "opened": opened, "older_presses": [s["after"] for s in left],
         "newer_presses": [s["after"] for s in came], "back": back})


async def case_log_eviction(lg: LogRun) -> None:
    lg.steady()
    await lg.jump()
    await asyncio.sleep(1.5)
    # Following: a burst past the cap. The trimmed note counts every exchange that left.
    e0 = await lg.probe()
    follow = await lg.burst(300)
    e1 = await lg.probe()
    left = (trimmed_count(e1) or 0) - (trimmed_count(e0) or 0)
    arrived = (last_seq(e1) or 0) - (last_seq(e0) or 0)
    lg.note("left", e1["trimmed"])
    acc_f = lg.account("following past the cap", e1)
    # Pinned: a burst until every row the reader had in the window has left the cap.
    await lg.wheel_up()
    a = await lg.probe()
    pin_last = a["seqs"][-1]
    pinned = await lg.burst(0, until=lambda p: bool(p["seqs"]) and p["seqs"][0] > pin_last
                            and W_REPIN in (p["trimmed"] or ""))
    z = await lg.probe()
    acc_p = lg.account("pinned, re-pinned after eviction", z)
    lg.note("left", z["trimmed"])
    lg.steady()
    await lg.jump()
    await asyncio.sleep(1.0)
    after_jump = await lg.probe()
    lg.record(
        5, "Eviction past the 2,000 cap",
        "Following, a burst of 300 at 50/s: the trimmed note's count rises by exactly the exchanges that arrived "
        "and reads '… left this view.' with 'The page keeps the newest 2,000 exchanges it received.'; 2,000 "
        "retained; 200 rows, the count line '200 of 2,000 shown', the Older row '1,800 older exchanges match your "
        "filters' (then any marker counts). Pinned by a real wheel scroll, a burst until every row of the "
        "reader's window has left the cap: at every 1 s sample the window holds 200 exchanges (never fewer), "
        "contiguous; it stays pinned; at the end it is at the oldest exchanges kept (no Older row) and the note "
        "adds '" + W_REPIN + "'; counts add up. Jump to newest then follows and the sentence goes",
        {"full buffer at the start": e0["retained"] == PERF_ROWS,
         "following: the trimmed count rises by the arrivals": arrived >= 300 and left == arrived,
         "following: the note's wording": (e1["trimmed"] or "").endswith(
             "left this view.The page keeps the newest 2,000 exchanges it received. The rows were received, so their "
             "removal is not a gap.") and re.match(r"[\d,]+ older rows", e1["trimmed"] or "") is not None,
         "following: 2,000 retained, 200 rows, the count line and the Older row": all(
             s["retained"] == PERF_ROWS and s["ex"] == LOG_WINDOW for s in follow + [e1])
         and e1["count"].startswith("200 of 2,000 shown")
         and re.match(r"1,800 older exchanges match your filters[.;]", e1["older"] or "") is not None,
         "following: counts add up": acc_f["adds_up"],
         "pinned: 200 rows at every sample, never fewer": bool(pinned) and min(s["ex"] for s in pinned) == LOG_WINDOW,
         "pinned: contiguous at every sample": all(not s["breaks"] for s in pinned),
         "pinned: still pinned at every sample": all(not s["follow"]["disabled"] for s in pinned + [z]),
         "pinned: every row of the reader's window left": z["seqs"][0] > pin_last,
         "pinned: at the oldest kept, the Newer row offered": z["older"] is None and z["newerButton"]
         and edge_count(z["newer"], "newer") > 0,
         "pinned: the note adds the re-pin sentence": W_REPIN in (z["trimmed"] or ""),
         "pinned: counts add up": acc_p["adds_up"],
         "Jump to newest: following, the sentence gone": after_jump["follow"]["disabled"]
         and W_REPIN not in (after_jump["trimmed"] or "")},
        {"retained": e0["retained"], "following": {"trimmed": [trimmed_count(e0), trimmed_count(e1)],
                                                   "arrived": arrived, "note": e1["trimmed"], "count": e1["count"],
                                                   "older": e1["older"], "accounting": acc_f},
         "pinned": {"window_at_pin": window_of(a), "samples": len(pinned),
                    "min_rows": min((s["ex"] for s in pinned), default=None),
                    "windows": [window_of(s) for s in pinned][::3], "end": window_of(z), "note": z["trimmed"],
                    "newer": z["newer"], "count": z["count"], "accounting": acc_p},
         "after_jump": [after_jump["count"], after_jump["trimmed"]]})


def mark_with(p: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    return next((x for x in reversed(p["marks"]) if x["text"].startswith(prefix)), None)


MARKS_RE = r"([\d,]+) gap markers? and ([\d,]+) connection notes? in {side} rows"


async def case_log_markers(lg: LogRun) -> None:
    lg.steady()
    await lg.jump()
    await asyncio.sleep(1.5)
    m0 = await lg.probe()
    conds: dict[str, bool] = {"full buffer at the start": m0["retained"] == PERF_ROWS}
    # A sequence gap: the harness's wrapper keeps 3 exchange events from the page.
    await lg.cdp.js("window.__lg.dropped = []; window.__lg.drop = 3")
    await lg.cdp.wait_for("[...document.querySelectorAll('#log-body tr.mark--gap')].some(r => "
                          "window.__lg.dropped.length === 3 && r.textContent.indexOf('seq ' + window.__lg.dropped[0]"
                          " + '–' + window.__lg.dropped[2] + ' ') >= 0)", timeout=20)
    await lg.settle(0.5)
    g = await lg.probe()
    dropped = await lg.cdp.js("window.__lg.dropped")
    gap = mark_with(g, f"Gap: seq {dropped[0]}–{dropped[2]} not received (3).")
    lg.note("gap", gap["text"] if gap else None)
    conds["the gap marker drawn where it falls, in the following window"] = (
        gap is not None and gap["prev"] == dropped[0] - 1 and gap["next"] == dropped[2] + 1
        and g["follow"]["disabled"])
    # A connection note: SIGSTOP / SIGCONT of the simulator.
    os.kill(lg.sim.pid, signal.SIGSTOP)
    lg.run.log(f"SIGSTOP the simulator (pid {lg.sim.pid})")
    try:
        await lg.cdp.wait_for(f"{CONN} !== 'Live'", timeout=15)
    finally:
        os.kill(lg.sim.pid, signal.SIGCONT)
        lg.run.log("SIGCONT the simulator")
    await lg.cdp.wait_for("document.getElementById('log-body').textContent.indexOf("
                          f"{json.dumps(W_CONN)}) >= 0", timeout=30)
    await asyncio.sleep(2.0)
    c = await lg.probe()
    conn = mark_with(c, W_CONN)
    after = re.search(r"after seq (\d+)\.", conn["text"]) if conn else None
    conds["the connection note drawn where it falls (after the exchange it names)"] = (
        conn is not None and after is not None and conn["prev"] == int(after.group(1)) and c["follow"]["disabled"])
    # Both outside a pinned window: counted beside Jump to newest.
    await lg.wheel_up()
    pressed = []
    for _ in range(8):
        q = await lg.probe()
        if mark_with(q, "Gap: seq") is None and mark_with(q, W_CONN) is None and q["newer"] \
                and re.search(MARKS_RE.format(side="newer"), q["newer"]):
            break
        pressed.append(await lg.nav("older", "enter"))
    n = await lg.probe()
    newer_m = re.search(MARKS_RE.format(side="newer"), n["newer"] or "")
    lg.note("gap", n["newer"])
    conds["outside the window (newer): not drawn, counted in the Newer row beside Jump to newest"] = (
        newer_m is not None and n_of(newer_m.group(1)) >= 1 and n_of(newer_m.group(2)) >= 1
        and mark_with(n, "Gap: seq") is None and mark_with(n, W_CONN) is None
        and await lg.cdp.js("!!document.querySelector('#lognav-newer:not([hidden]) [data-lognav=\"newest\"]')"))
    acc_n = lg.account("markers newer than a pinned window", n)
    jumped = await lg.nav("newest", "click")
    conds["the in-row Jump to newest follows again"] = jumped["following"] and jumped["focus"] == "logwrap"
    # Older than the following window, after a burst: counted beside Older.
    await lg.burst(LOG_WINDOW + 60)
    lg.steady()
    await asyncio.sleep(1.0)
    o = await lg.probe()
    older_m = re.search(MARKS_RE.format(side="older"), o["older"] or "")
    conds["outside the window (older): not drawn, counted in the Older row"] = (
        older_m is not None and n_of(older_m.group(1)) >= 1 and n_of(older_m.group(2)) >= 1
        and mark_with(o, f"Gap: seq {dropped[0]}") is None and o["follow"]["disabled"])
    acc_o = lg.account("markers older than the following window", o)
    conds["counts add up"] = acc_n["adds_up"] and acc_o["adds_up"]
    # Across a restart: SIGTERM, a new simulator; the restart marker, and the count line says so.
    lg.quiet()
    lg.run.log("SIGTERM the simulator, then start a new one")
    lg.run.stop(lg.sim)
    await lg.cdp.wait_for(f"{CONN} !== 'Live'", timeout=15)
    lg.sims += 1
    lg.sim = await m3b_start(lg.run, MOVING_PROFILE, f"log-simulator-{lg.sims}.log")
    await lg.cdp.wait_for("document.body.dataset.health === 'live'", timeout=30)
    lg.steady()
    await lg.cdp.wait_for("(() => { const rows = [...document.querySelectorAll('#log-body > tr')];"
                          " const i = rows.findLastIndex(r => r.textContent.startsWith('Simulator restarted.'));"
                          " return i >= 0 && rows.slice(i + 1).filter(r => r.classList.contains('ex')).length >= 5;"
                          " })()",
                          timeout=30)
    await lg.settle(0.5)
    r = await lg.probe()
    restart = mark_with(r, W_RESTART)
    across = re.search(rf"seq ([\d,]+) … ([\d,]+) {W_ACROSS}", r["count"])
    conds["the restart marker drawn between the two runs' rows"] = (
        restart is not None and restart["prev"] is not None and restart["next"] is not None
        and restart["next"] < restart["prev"])
    conds["across a restart the count line says so"] = (
        across is not None and n_of(across.group(1)) == r["seqs"][0] and n_of(across.group(2)) == r["seqs"][-1])
    lg.record(
        6, "Reconnect and gap markers",
        "Full buffer, following, standard traffic. 3 exchange events kept from the page: 'Gap: seq a–b not received "
        "(3).' drawn between seq a-1 and b+1. SIGSTOP / SIGCONT: 'Connection lost, then resumed.' drawn after the "
        "exchange it names ('after seq N'). Pinned and moved older (Older by Enter) until both are newer than the "
        "window: neither is drawn and the Newer row, beside its Jump to newest, reads 'N gap marker(s) and N "
        "connection note(s) in newer rows'; the in-row Jump to newest follows. A burst of 260 makes them older "
        "than the following window: not drawn, the Older row reads '… in older rows'. Counts add up. SIGTERM and a "
        "new simulator: 'Simulator restarted.' drawn between the old run's rows and the new run's (seq goes back), "
        "and the count line reads 'seq A … B across a restart' with A and B the window's first and last seq",
        conds,
        {"retained": m0["retained"], "dropped": dropped, "gap": gap, "connection_note": conn,
         "older_presses": [s["after"] for s in pressed], "newer_row": n["newer"], "newer_window": window_of(n),
         "older_row": o["older"], "older_window": window_of(o), "jump": {k: jumped[k] for k in ("after", "following")},
         "restart": restart, "count_after_restart": r["count"], "window_after_restart": window_of(r),
         "accounting": [acc_n, acc_o]})


# The four kinds of absent rows: each kind's own phrase, and the phrases that belong to the others.
ABSENT_KINDS = {"outside": W_MATCH, "hidden": W_HIDDEN, "gap": W_GAP, "left": W_LEFT}


def case_log_wording(lg: LogRun) -> None:
    conds: dict[str, bool] = {}
    first: dict[str, str] = {}
    for kind, phrase in ABSENT_KINDS.items():
        texts = lg.seen.get(kind, [])
        # Each kind's own row: the first text seen in its situation that carries its phrase.
        own = next((t for t in texts if phrase in t or (kind == "outside" and W_BEYOND in t)), None)
        conds[f"{kind}: seen, in its own wording"] = own is not None
        if own is not None:
            first[kind] = own
    # The outside kind's own row is an unfiltered Older row (it may also count markers there).
    singles = {"outside": next((t for t in lg.seen.get("outside", []) if re.match(
        r"[\d,]+ older exchanges match your filters[.;]", t) and "hidden" not in t), None),
        "hidden": next((t for t in lg.seen.get("hidden", []) if re.fullmatch(
            rf"[\d,]+ exchanges? {re.escape(W_HIDDEN)}", t)), None),
        "gap": next((t for t in lg.seen.get("gap", []) if t.startswith("Gap: seq")), None),
        "left": next((t for t in lg.seen.get("left", []) if re.match(r"[\d,]+ older rows", t)), None)}
    for kind, text in singles.items():
        others = [p for k, p in ABSENT_KINDS.items() if k != kind]
        conds[f"{kind}: its own row carries no other kind's phrase"] = text is not None and not any(
            p in text for p in others) and (kind != "outside" or "hidden" not in text)
    conds["the four phrases differ"] = len(set(ABSENT_KINDS.values())) == 4
    bad = [a for a in lg.accounts if not a["adds_up"]]
    conds["counts add up in every state read (matching = older + shown + newer)"] = bool(lg.accounts) and not bad
    lg.record(
        8, "The four absent-row wordings",
        "Over this width's cases: outside this window ('… match your filters', 'beyond this window'), hidden by "
        "filters ('… hidden by filters (not a gap)'), a sequence gap ('Gap: seq … not received'), and the rows "
        "that left the 2,000 cap ('… left this view.') each appear in their own situation, and each kind's own row "
        "carries none of the others' phrases. In every state read, the count line's matching = the Older row's "
        "count + the rows drawn + the Newer row's count, and the count line's shown = the exchange rows drawn",
        conds, {"seen": {k: v[:4] for k, v in lg.seen.items()}, "own_rows": singles, "first": first,
                "accounts": lg.accounts, "not_adding_up": bad})


async def case_log_layout(lg: LogRun) -> None:
    conds = {f"no horizontal overflow ({o['state']})": not o["over"] for o in lg.overflows}
    observed: dict[str, Any] = {"overflow": lg.overflows}
    rule = "No scrollWidth above its clientWidth (OVERFLOW, #log-panel, #logwrap, #log-state) in every state measured"
    if lg.size == WIDE:
        lg.steady()
        await lg.jump()
        await lg.cdp.js("window.scrollTo(0, 0)")
        readings = []
        for k in range(3):
            if k:
                await asyncio.sleep(1.0)
            await lg.settle(0.0)
            readings.append(await lg.cdp.js(LOG_ROWS))
        rows = min(readings, key=lambda r: r["full"])
        conds["graphs open"] = (await lg.cdp.js("!document.getElementById('graphs').hidden")) is True
        conds["at least 5 full log rows (1440 x 900, graphs open, full buffer)"] = rows["full"] >= 5
        observed["log_rows"] = {"fewest": rows["full"], "readings": [r["full"] for r in readings],
                                "rowsRegion": rows["rowsRegion"], "heights": rows["heights"][-6:]}
        rule += "; at 1440 x 900 at least 5 full log rows inside #logwrap with the graphs open (LOG_ROWS, the fewest " \
                "of 3 readings)"
    conds["at least one state measured"] = bool(lg.overflows)
    lg.record(9, "Layout", rule, conds, observed)


async def log_width(lg: LogRun, label: str, size: tuple[int, int]) -> None:
    lg.label, lg.size = label, size
    lg.seen, lg.accounts, lg.overflows = {}, [], []
    lg.run.log(f"---- --m3b-log at {label} ----")
    await lg.cdp.viewport(*size)
    await asyncio.sleep(1.5)
    await lg.cdp.js("document.getElementById('logwrap').scrollIntoView({block: 'center'})"
                    if size == NARROW else "window.scrollTo(0, 0)")
    await lg.jump()
    if (await lg.probe())["retained"] < PERF_ROWS:
        await lg.burst(0, until=lambda p: p["retained"] >= PERF_ROWS)
    await lg.unique(LOG_UNIQUE)
    await lg.burst(LOG_AFTER_UNIQUE)
    lg.steady()
    await asyncio.sleep(3.0)
    start = await lg.probe()
    lg.run.log(f"width start: {start['count']}; retained {start['retained']}")
    await case_log_following(lg)
    await case_log_pause(lg)
    await case_log_filters(lg)
    await case_log_navigation(lg)
    await case_log_expansion(lg)
    await case_log_eviction(lg)
    await case_log_markers(lg)
    case_log_wording(lg)
    await case_log_layout(lg)


async def m3b_log_session(run: Run, chrome: str, profile_dir: str) -> None:
    """Task 46a: the windowed log's cases at both widths, each from a full 2,000-exchange buffer."""
    import hashlib

    await launch_chrome(run, chrome, profile_dir)
    cases = Cases(run)
    problems: list[str] = []
    version = "unknown"
    served: dict[str, Any] = {}
    async with aiohttp.ClientSession() as http:
        ws_url = await page_target(http)
        version = await chrome_version(http)
        run.log(f"Chrome: {version}")
        async with http.ws_connect(ws_url, max_msg_size=0) as ws:
            cdp = await m3b_cdp(run, http, ws, problems, wrapper=False)
            await cdp.send("Page.addScriptToEvaluateOnNewDocument", source=LOG_INSTR)
            m = M3b(run, cdp, http, cases)
            lg = LogRun(run, m, await m3b_start(run, MOVING_PROFILE, "log-simulator-1.log"))
            try:
                async with http.get(f"http://{API}/app.js") as resp:
                    text = await resp.text()
                served = {"app_js_sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "wording_missing": [w for w in LOG_WORDING if w not in text]}
                run.log(f"served app.js: {served}")
                cases.record("page: the wording the cases read is the served page's",
                             "Every phrase the cases match is in the app.js the simulator served",
                             {"no phrase missing": not served["wording_missing"]}, served)
                await m.open_page()
                await m.click_window(120)
                run.log(f"fill: traffic at {LOG_BURST_RATE}/s until the log holds {PERF_ROWS} exchanges")
                await lg.burst(0, until=lambda p: p["retained"] >= PERF_ROWS, timeout=300)
                for label, size in (("1440x900", WIDE), ("390x844", NARROW)):
                    await log_width(lg, label, size)
            finally:
                with contextlib.suppress(ProcessLookupError):
                    if lg.sim.poll() is None:
                        os.kill(lg.sim.pid, signal.SIGCONT)
                lg.quiet()
                run.stop(lg.sim)
                cases.record("page: no exception or console message", "Over the whole run: no page exception, "
                             "console message or browser log entry", {"none": not problems}, {"problems": problems})
                tally = {}
                for label in ("1440x900", "390x844", "page"):
                    mine = [c for c in cases.items if c["case"].startswith(label)]
                    tally[label] = {"passed": sum(1 for c in mine if c["pass"]), "total": len(mine)}
                out = {"chrome": version, "served": served, "rows_cap": PERF_ROWS, "traffic_rate": TRAFFIC_RATE,
                       "profile": str(MOVING_PROFILE.relative_to(ROOT)), "cases": cases.items, "tally": tally,
                       "problems": problems}
                (run.outdir / "m3b-log-results.json").write_text(json.dumps(out, indent=1, default=str))
                run.log(f"log cases: {json.dumps(tally)}")
                run.log(f"console messages, exceptions and browser log entries: {len(problems)}")
                for problem in problems:
                    run.log(f"  {problem}")
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
    modes.add_argument("--m3b-perf", action="store_true",
                       help="the main-thread comparison runs with traces (Task 39, about 22 min)")
    modes.add_argument("--m3b-perf-log", action="store_true",
                       help="the log fix's before / after runs with clicks and Event Timing (Task 44, about 7 min)")
    modes.add_argument("--m3b-log", action="store_true",
                       help="the windowed exchange log's cases at a full buffer, 1440 and 390 (Task 46a)")
    args = parser.parse_args(argv)
    mode = ("moving" if args.moving else "m3b" if args.m3b else "m3b-long" if args.m3b_long
            else "m3b-slots" if args.m3b_slots else "m3b-perf" if args.m3b_perf
            else "m3b-perf-log" if args.m3b_perf_log else "m3b-log" if args.m3b_log else "m3a")
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
