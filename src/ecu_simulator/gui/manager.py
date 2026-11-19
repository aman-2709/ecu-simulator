"""Own one simulator child on the local server's event loop."""

import asyncio
import json
import sys
from contextlib import suppress
from typing import Any
from uuid import uuid4

from ecu_simulator.app import endpoint_name
from ecu_simulator.config import EndpointConfigModel, load_profile_text


class RunConflict(ValueError):
    """The requested operation conflicts with the active run."""


class Manager:
    def __init__(self, *, child_command: tuple[str, ...] | None = None, startup_timeout: float = 8,
                 stop_timeout: float = 2) -> None:
        self.command = child_command or (sys.executable, "-m", "ecu_simulator.gui.child")
        self.startup_timeout = startup_timeout
        self.stop_timeout = stop_timeout
        self.lock = asyncio.Lock()
        self.state = "Stopped"
        self.id: str | None = None
        self.interface = ""
        self.error = ""
        self.endpoints: dict[str, EndpointConfigModel] = {}
        self.process: asyncio.subprocess.Process | None = None
        self.watcher: asyncio.Task[None] | None = None
        self.cleanup: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return dict(state=self.state, id=self.id, error=self.error, interface=self.interface,
                    endpoints=list(self.endpoints))

    async def start(self, text: str) -> dict[str, Any]:
        async with self.lock:
            if self.state not in {"Stopped", "Failed"}:
                raise RunConflict("A run is already active")
            profile = load_profile_text(text)
            self.id = str(uuid4())
            self.interface = profile.transport.interface
            self.endpoints = {endpoint_name(key, ep): ep for key, ecu in profile.ecus.items()
                              for ep in ecu.endpoints if not ep.functional and "uds" in ep.protocols}
            self.state, self.error = "Starting", ""
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
            except OSError as error:
                self.state, self.error = "Failed", str(error)
            else:
                self.watcher = asyncio.create_task(self._watch(self.process, self.id, text))
            return self.status()

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), self.stop_timeout)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
        await proc.wait()

    async def _watch(self, proc: asyncio.subprocess.Process, run_id: str, text: str) -> None:
        assert proc.stdin and proc.stdout
        try:
            async with asyncio.timeout(self.startup_timeout):
                proc.stdin.write(text.encode("utf-8"))
                try:
                    await proc.stdin.drain()
                finally:
                    proc.stdin.close()
                line = await proc.stdout.readline()
                if not line:
                    raise RuntimeError("ECU exited before reporting readiness")
                event = json.loads(line)
                if event.get("ready") is not True:
                    raise RuntimeError(event.get("error") or "Invalid ECU startup status")
            if self.id == run_id and self.state == "Starting":
                self.state = "Running"
            code = await proc.wait()
            reason = f"ECU exited with status {code}"
        except TimeoutError:
            reason = "ECU startup timed out"
        except Exception as error:
            reason = str(error) or type(error).__name__
        await self._terminate(proc)
        if self.id == run_id and self.state != "Stopping":
            self.state, self.error = "Failed", reason

    def _require_current(self, run_id: str) -> None:
        if not run_id:
            raise ValueError("A run ID is required")
        if run_id != self.id:
            raise RunConflict("The displayed run is stale. Review the current run and try again.")

    async def stop(self, run_id: str) -> dict[str, Any]:
        async with self.lock:
            self._require_current(run_id)
            if self.state not in {"Starting", "Running"}:
                raise RunConflict("This run cannot be stopped in its current state")
            self.state = "Stopping"
            self.cleanup = asyncio.create_task(self._stop())
        await asyncio.shield(self.cleanup)
        return self.status()

    async def _stop(self) -> None:
        assert self.process and self.watcher
        await self._terminate(self.process)
        await self.watcher
        self.state = "Stopped"

    async def close(self) -> None:
        if self.state in {"Starting", "Running"} and self.id:
            await self.stop(self.id)
        elif self.cleanup is not None:
            await self.cleanup
