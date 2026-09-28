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
IFACE = "vcan0"
API = "127.0.0.1:8765"
PAGE = f"http://{API}/"
DEVTOOLS_PORT = 9222
WIDE = (1440, 900)
NARROW = (390, 844)
TRAFFIC_RATE = "4"


class Run:
    """The processes of one demo run, each stopped by its exact PID."""

    def __init__(self, outdir: Path) -> None:
        self.outdir = outdir
        self.procs: list[subprocess.Popen[bytes]] = []
        self.t0 = time.monotonic()
        self.logfile = (outdir / "capture.log").open("a")

    def log(self, message: str) -> None:
        line = f"[{time.monotonic() - self.t0:7.2f} s] {message}"
        print(line, flush=True)
        self.logfile.write(line + "\n")
        self.logfile.flush()

    def spawn(self, cmd: list[str], logname: str) -> subprocess.Popen[bytes]:
        out = (self.outdir / logname).open("ab")
        proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=ROOT)
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
FOLLOW_TEXT = "(function(){var b=document.getElementById('btn-follow'); return b.hidden ? '' : b.textContent;})()"


async def launch_chrome(run: Run, chrome: str, profile_dir: str) -> subprocess.Popen[bytes]:
    run.log(f"start headless Chrome: {chrome}")
    proc = run.spawn([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                      "--no-first-run", "--no-default-browser-check", f"--user-data-dir={profile_dir}",
                      f"--remote-debugging-port={DEVTOOLS_PORT}", "about:blank"], "chrome.log")
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


async def demo(run: Run, chrome: str) -> None:
    profile_dir = tempfile.mkdtemp(prefix="gui-demo-chrome-")
    try:
        await session(run, chrome, profile_dir)
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
            # A real wheel scroll up: following stops and new rows are counted below.
            await cdp.wheel("#logwrap", -700)
            await asyncio.sleep(3.0)
            run.log(f"after the wheel scroll up, the follow control reads: {await cdp.js(FOLLOW_TEXT)!r}")
            await cdp.shot(run, "d4-scrolled-up-new-rows-below.png")
            await cdp.click("#btn-follow")
            await asyncio.sleep(1.5)
            run.log(f"after clicking it, the follow control reads: {await cdp.js(FOLLOW_TEXT)!r}")
            await cdp.shot(run, "d5-jumped-to-newest.png")

            # c. The DTC panel: P0128 pending at t = 40 s; confirmed with the MIL at t = 75 s.
            await until_scenario(44)
            await cdp.shot(run, "c1-dtc-pending.png")
            await until_scenario(80)
            await cdp.shot(run, "c2-dtc-confirmed-mil.png")

            # g. One narrow width, the whole page.
            await cdp.viewport(*NARROW)
            await asyncio.sleep(1.5)
            await cdp.shot(run, "g1-narrow-390.png")
            await cdp.shot(run, "g2-narrow-390-full-page.png", full_page=True)
            await cdp.viewport(*WIDE)
            await asyncio.sleep(1.0)

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


def find_chrome() -> str:
    for name in (os.environ.get("CHROME", ""), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if name and (path := shutil.which(name)):
            return path
    raise SystemExit("no Chrome or Chromium found; set CHROME=/path/to/chrome")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("outdir", type=Path, help="directory for the screenshots and logs")
    args = parser.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    run = Run(args.outdir.resolve())
    run.log(f"gui demo capture, {datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')}")

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        asyncio.run(demo(run, find_chrome()))
    except KeyboardInterrupt:
        print("interrupted; every process was stopped", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
