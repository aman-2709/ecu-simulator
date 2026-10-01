#!/usr/bin/env python3
"""The M3b fault-injection test server (design gui-m3b-graphs-design.md §12.3) -- a test harness.

It is not package data and the shipped simulator never serves it. It runs the real
``app.run(..., api=...)`` in-process, from a shipped profile or the stepped demo, so the page
talks to the real ``ApiServer``, and schedules faults in-process through public seams only:

- ``--nonfinite PATH:START:END``: ``app.build_runtime`` is wrapped to capture the runtime, and
  ``runtime.runner.apply`` is replaced on that instance (``ScenarioSync`` calls it), so after
  each scenario apply ``PATH`` is set to ``nan`` through ``VehicleState.set`` while scenario
  ``t`` is in ``[START, END)``. With no scenario it is set once on a wall-time timer, ``START``
  seconds after the runtime is built, and put back at ``END``.
- ``--state-fault START:END[:vehicle|dtcs]``: the public module functions
  ``snapshots.state_message`` (the full snapshot, the default), ``snapshots.vehicle`` or
  ``snapshots.dtcs`` are replaced, so the snapshot the server's state task calls raises while
  the window is open. Otherwise each returns the real result, so text recovered with nothing
  changed is genuinely identical. The window is in scenario time with a scenario, and in wall
  time since the runtime was built without one.

Both options may be given more than once. Nothing under ``src/`` changes, and no profile file
holds a non-finite value. No traffic is sent: a ``01 05`` request during a non-finite window
would reach the OBD encoder (DEV-26). Like the capture script, it refuses to run outside
scripts/run_gui_demo.sh's private network namespace.

    gui_fault_server.py --profile PROFILE --interface vcan0 --api 127.0.0.1:8765 \\
        [--nonfinite engine.coolant_temp:10:20] [--state-fault 30:35] [--state-fault 80:90:dtcs]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecu_simulator import app
from ecu_simulator.api.options import ApiStartupError, parse_api
from ecu_simulator.cli import package_version
from ecu_simulator.config import ConfigError, load_profile
from ecu_simulator.loggers import logger_app
from ecu_simulator.observe import snapshots
from ecu_simulator.transport import TransportError

log = logging.getLogger("ecu_simulator.fault_server")
PARTS = ("state", "vehicle", "dtcs")


@dataclass(frozen=True)
class Window:
    start: float
    end: float

    def open_at(self, t: float | None) -> bool:
        return t is not None and self.start <= t < self.end


def parse_window(text: str, what: str) -> tuple[Window, str]:
    parts = text.split(":")
    try:
        start, end = float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        raise argparse.ArgumentTypeError(f"{what} expects START:END, got {text!r}") from None
    if not 0 <= start < end:
        raise argparse.ArgumentTypeError(f"{what} needs 0 <= START < END, got {text!r}")
    return Window(start, end), ":".join(parts[2:])


def nonfinite_option(text: str) -> tuple[str, Window]:
    path, sep, rest = text.partition(":")
    if not sep or not path:
        raise argparse.ArgumentTypeError(f"--nonfinite expects PATH:START:END, got {text!r}")
    window, extra = parse_window(rest, "--nonfinite")
    if extra:
        raise argparse.ArgumentTypeError(f"--nonfinite expects PATH:START:END, got {text!r}")
    return path, window


def state_fault_option(text: str) -> tuple[Window, str]:
    window, part = parse_window(text, "--state-fault")
    part = part or "state"
    if part not in PARTS:
        raise argparse.ArgumentTypeError(f"--state-fault part must be vehicle or dtcs, got {part!r}")
    return window, part


class Harness:
    """The captured runtime and the fault clock. Every fault is applied on the event loop."""

    def __init__(self, nonfinite: list[tuple[str, Window]], state_faults: list[tuple[Window, str]]) -> None:
        self.nonfinite = nonfinite
        self.state_faults = state_faults
        self.runtime: app.Runtime | None = None
        self.built_at: float | None = None          # monotonic, when the runtime was built
        self.saved: dict[str, Any] = {}              # the value a no-scenario PATH had before nan
        self.open_state: dict[tuple[Window, str], bool] = {}

    def now(self) -> float | None:
        """Fault time: scenario time with a scenario, wall time since the build without one."""
        if self.runtime is None or self.built_at is None:
            return None
        if self.runtime.sync is not None:
            return self.runtime.sync.elapsed
        return time.monotonic() - self.built_at

    def install(self) -> None:
        real_build = app.build_runtime

        def build_runtime(config: app.RuntimeConfig, clock: Any = None) -> app.Runtime:
            runtime = real_build(config, clock)
            self.runtime, self.built_at = runtime, time.monotonic()
            log.info("fault server: runtime captured (%s), wall origin %.3f",
                     "scenario time" if runtime.sync is not None else "no scenario: wall time", time.time())
            if runtime.runner is not None and self.nonfinite:
                self._wrap_apply(runtime)
            return runtime

        app.build_runtime = build_runtime  # type: ignore[assignment]
        for part in {part for _, part in self.state_faults}:
            name = "state_message" if part == "state" else part
            setattr(snapshots, name, self._faulty(part, getattr(snapshots, name)))

    def _wrap_apply(self, runtime: app.Runtime) -> None:
        runner = runtime.runner
        assert runner is not None
        real_apply = runner.apply

        def apply(t: float) -> None:
            real_apply(t)
            for path, window in self.nonfinite:
                if window.open_at(t):
                    if path not in self.saved:
                        self.saved[path] = runtime.vehicle.get(path)
                        log.info("fault server: %s set to nan from scenario t = %.3f", path, t)
                    runtime.vehicle.set(path, math.nan)
                elif path in self.saved and t >= window.end:
                    before = self.saved.pop(path)
                    value = runtime.vehicle.get(path)
                    # A path the scenario drives was already rewritten by real_apply; one it
                    # does not drive gets its value back.
                    if isinstance(value, float) and math.isnan(value):
                        runtime.vehicle.set(path, before)
                    log.info("fault server: %s valid again from scenario t = %.3f (%r)", path, t,
                             runtime.vehicle.get(path))

        runner.apply = apply  # type: ignore[method-assign]

    def _faulty(self, part: str, real: Callable[..., Any]) -> Callable[..., Any]:
        windows = [w for w, p in self.state_faults if p == part]

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t = self.now()
            for window in windows:
                is_open = window.open_at(t)
                if is_open != self.open_state.get((window, part), False):
                    self.open_state[(window, part)] = is_open
                    log.info("fault server: %s fault [%g, %g) %s at t = %.3f", part, window.start, window.end,
                             "opens" if is_open else "closes", t)
                if is_open:
                    raise RuntimeError(f"fault injected by gui_fault_server: {part} in [{window.start:g}, "
                                       f"{window.end:g}) at t = {t:.3f}")
            return real(*args, **kwargs)

        return wrapper

    async def no_scenario_timers(self) -> None:
        """--nonfinite with no scenario: set once at START, put back at END, wall time."""
        runtime = self.runtime
        if runtime is None or runtime.runner is not None:
            return
        events = sorted([(w.start, path, True) for path, w in self.nonfinite]
                        + [(w.end, path, False) for path, w in self.nonfinite])
        for at, path, invalid in events:
            delay = at - (self.now() or 0.0)
            if delay > 0:
                await asyncio.sleep(delay)
            if invalid:
                self.saved[path] = runtime.vehicle.get(path)
                runtime.vehicle.set(path, math.nan)
                log.info("fault server: %s set to nan at wall t = %.3f", path, self.now())
            elif path in self.saved:
                runtime.vehicle.set(path, self.saved.pop(path))
                log.info("fault server: %s restored at wall t = %.3f", path, self.now())


async def serve(harness: Harness, config: app.RuntimeConfig, api: Any) -> None:
    run = asyncio.create_task(app.run(config, api=api))
    timers: asyncio.Task[None] | None = None
    while not run.done() and harness.runtime is None:
        await asyncio.sleep(0.01)
    if harness.nonfinite:
        timers = asyncio.create_task(harness.no_scenario_timers())
    try:
        await run
    finally:
        if timers is not None:
            timers.cancel()


def main(argv: list[str] | None = None) -> int:
    host = os.environ.get("GUI_DEMO_HOST_NETNS")
    here = os.readlink("/proc/self/ns/net")
    if not host or host == here:
        raise SystemExit("gui_fault_server.py runs only inside scripts/run_gui_demo.sh's private network "
                         f"namespace (host {host or 'unknown'}, here {here}); refusing")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--interface", default="vcan0")
    parser.add_argument("--api", default="127.0.0.1:8765")
    parser.add_argument("--nonfinite", type=nonfinite_option, action="append", default=[],
                        metavar="PATH:START:END")
    parser.add_argument("--state-fault", type=state_fault_option, action="append", default=[],
                        metavar="START:END[:vehicle|dtcs]")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args(argv)
    logger_app.configure(getattr(logging, args.log_level))
    try:
        profile = load_profile(args.profile)
        api = parse_api(args.api, str(args.profile), package_version())
    except (ConfigError, ApiStartupError) as error:
        log.error("%s", error)
        return 2
    config = app.RuntimeConfig.build(profile, args.interface)
    for path, _ in args.nonfinite:
        if path not in app.build_vehicle(config).signals:
            log.error("--nonfinite: %r is not a signal of this vehicle", path)
            return 2
    harness = Harness(args.nonfinite, args.state_fault)
    harness.install()
    log.info("fault server: nonfinite %s, state faults %s",
             [(p, w.start, w.end) for p, w in args.nonfinite], [(w.start, w.end, p) for w, p in args.state_fault])
    try:
        asyncio.run(serve(harness, config, api))
    except (TransportError, ApiStartupError) as error:
        log.error("%s", error)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
