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
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import itertools
import json
import os
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
TRAFFIC_RATE = "4"


class Run:
    """The processes of one demo run, each stopped by its exact PID."""

    def __init__(self, outdir: Path) -> None:
        self.outdir = outdir
        self.procs: list[subprocess.Popen[bytes]] = []
        self.t0 = time.monotonic()
        self.logfile = (outdir / "capture.log").open("a")
        self.overflow_failures: list[str] = []

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
OVERFLOW = """(function(){
  var v = document.getElementById('vehicle');
  return [['#vehicle', v], ['html', document.documentElement], ['body', document.body]].map(function (p) {
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


async def launch_chrome(run: Run, chrome: str, profile_dir: str) -> subprocess.Popen[bytes]:
    run.log(f"start headless Chrome: {chrome}")
    proc = run.spawn([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                      "--no-first-run", "--no-default-browser-check", f"--user-data-dir={profile_dir}",
                      f"--remote-debugging-port={DEVTOOLS_PORT}", "about:blank"], "chrome.log",
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


async def demo(run: Run, chrome: str, moving: bool = False) -> None:
    profile_dir = tempfile.mkdtemp(prefix="gui-demo-chrome-")
    try:
        await (moving_session if moving else session)(run, chrome, profile_dir)
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
    parser.add_argument("--moving", action="store_true",
                        help="the moving-vehicle set (docs/examples/ice_drive_cycle_stepped.yaml)")
    args = parser.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    run = Run(args.outdir.resolve())
    run.log(f"gui demo capture, {datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')}")

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        asyncio.run(demo(run, find_chrome(), args.moving))
    except KeyboardInterrupt:
        print("interrupted; every process was stopped", file=sys.stderr)
        return 130
    if run.overflow_failures:
        print("horizontal overflow at: " + "; ".join(run.overflow_failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
