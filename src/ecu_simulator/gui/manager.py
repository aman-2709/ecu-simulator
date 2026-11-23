"""Own one simulator child on the local server's event loop."""

import asyncio
import json
import sys
import time
from contextlib import suppress
from typing import Any
from uuid import uuid4

from ecu_simulator.app import endpoint_name
from ecu_simulator.config import EndpointConfigModel, load_profile_text
from ecu_simulator.gui.tester import exchange

# The child reports a car run's metadata in its ready line, about 220 bytes per message: past asyncio's default 64 KiB
# for a bus of about 300 messages. 16 MiB holds about 70,000.
EVENT_LINE_LIMIT = 16 * 1024 * 1024


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
        self.replies: dict[str, EndpointConfigModel] = {}
        self.process: asyncio.subprocess.Process | None = None
        self.watcher: asyncio.Task[None] | None = None
        self.exchange: asyncio.Task[dict[str, Any]] | None = None
        self.restbus: dict[str, Any] | None = None
        self.dbc: dict[str, Any] | None = None
        self.car: dict[str, Any] | None = None
        self.broadcast_seq = 0
        self.broadcast_ack: asyncio.Future[bool] | None = None
        self.cleanup: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return dict(state=self.state, id=self.id, error=self.error, interface=self.interface,
                    endpoints=list(self.endpoints), dbc=self.dbc if self.state == "Running" else None,
                    restbus=self.restbus if self.state == "Running" else None,
                    car=self.car if self.state == "Running" else None)

    async def start(self, text: str) -> dict[str, Any]:
        async with self.lock:
            if self.state not in {"Stopped", "Failed"}:
                raise RunConflict("A run is already active")
            profile = load_profile_text(text)
            self.id = str(uuid4())
            self.interface = profile.transport.interface
            self.endpoints = {endpoint_name(key, ep): ep for key, ecu in profile.ecus.items()
                              for ep in ecu.endpoints if not ep.functional and "uds" in ep.protocols}
            self.restbus = None
            self.dbc = None
            self.car = None
            self.replies = {}
            for key, ecu in profile.ecus.items():
                by_name = {ep.name: ep for ep in ecu.endpoints}
                for ep in ecu.endpoints:
                    self.replies[endpoint_name(key, ep)] = by_name[ep.reply_via] if ep.reply_via else ep
            self.state, self.error = "Starting", ""
            started_us = time.time_ns() // 1000
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    limit=EVENT_LINE_LIMIT)
            except OSError as error:
                self.state, self.error = "Failed", str(error)
            else:
                self.watcher = asyncio.create_task(self._watch(self.process, self.id, text, started_us))
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

    async def _watch(self, proc: asyncio.subprocess.Process, run_id: str, text: str, started_us: int) -> None:
        assert proc.stdin and proc.stdout
        try:
            async with asyncio.timeout(self.startup_timeout):
                proc.stdin.write(json.dumps({"profile": text}).encode() + b"\n")
                await proc.stdin.drain()
                line = await proc.stdout.readline()
                if not line:
                    raise RuntimeError("ECU exited before reporting readiness")
                event = json.loads(line)
                if event.get("ready") is not True:
                    raise RuntimeError(event.get("error") or "Invalid ECU startup status")
            if self.id == run_id and self.state == "Starting":
                self.dbc = event.get("dbc")
                self.car = event.get("car")
                if event.get("restbus"):
                    self.restbus = {**event["restbus"], "paused": False, "started_us": started_us}
                self.state = "Running"
            while line := await proc.stdout.readline():
                event = json.loads(line)
                if "error" in event:
                    raise RuntimeError(event["error"])
                if type(event.get("paused")) is bool and self.restbus is not None:
                    self.restbus["paused"] = event["paused"]
                    ack = self.broadcast_ack
                    if event.get("sequence") == self.broadcast_seq and ack is not None and not ack.done():
                        ack.set_result(event["paused"])
            reason = f"ECU exited with status {await proc.wait()}"
        except TimeoutError:
            reason = "ECU startup timed out"
        except Exception as error:
            reason = str(error) or type(error).__name__
        if self.broadcast_ack is not None and not self.broadcast_ack.done():
            self.broadcast_ack.set_exception(RunConflict(reason))
        proc.stdin.close()
        await self._terminate(proc)
        if self.id == run_id and self.state != "Stopping":
            self.state = "Stopping"
            await self._cancel_exchange()
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

    async def broadcast(self, run_id: str, paused: bool) -> dict[str, Any]:
        async with self.lock:
            self._require_current(run_id)
            if self.state != "Running" or self.restbus is None:
                raise RunConflict("This run has no active wheel-speed publisher")
            assert self.process and self.process.stdin
            self.broadcast_seq += 1
            self.broadcast_ack = asyncio.get_running_loop().create_future()
            try:
                command = {"paused": paused, "sequence": self.broadcast_seq}
                self.process.stdin.write(json.dumps(command).encode() + b"\n")
                async with asyncio.timeout(self.stop_timeout):
                    await self.process.stdin.drain()
                    if await self.broadcast_ack is not paused:
                        raise RunConflict("Unexpected publisher acknowledgement")
            except (TimeoutError, ConnectionError) as error:
                # Tear down an unresponsive child before any later command could consume its late reply.
                await self._terminate(self.process)
                raise RunConflict("ECU did not acknowledge the publisher command") from error
            finally:
                self.broadcast_ack = None
            return self.status()

    async def send(self, run_id: str, endpoint: str, hex_text: str) -> dict[str, Any]:
        async with self.lock:
            self._require_current(run_id)
            if self.state != "Running":
                raise RunConflict("The ECU must be running")
            if self.exchange is not None:
                raise RunConflict("Another tester exchange is in progress")
            if endpoint not in self.endpoints:
                raise ValueError("Select a physical UDS endpoint from the running profile")
            try:
                payload = bytes.fromhex(hex_text)
            except ValueError:
                raise ValueError("Enter complete hexadecimal byte pairs") from None
            if not payload:
                raise ValueError("Enter request bytes")
            request = exchange(self.interface, self.endpoints[endpoint], payload, self.replies[endpoint])
            task = asyncio.create_task(request)
            self.exchange = task
        try:
            result = await task
            return {**result, "id": run_id, "request": payload.hex(" ").upper()}
        except asyncio.CancelledError:
            if task.cancelled():
                raise RunConflict("Exchange cancelled") from None
            raise
        finally:
            if self.exchange is task:
                self.exchange = None

    async def _cancel_exchange(self) -> None:
        if self.exchange is not None:
            self.exchange.cancel()
            await asyncio.gather(self.exchange, return_exceptions=True)

    async def _stop(self) -> None:
        assert self.process and self.watcher
        await self._cancel_exchange()
        await self._terminate(self.process)
        await self.watcher
        self.state = "Stopped"

    async def close(self) -> None:
        if self.state in {"Starting", "Running"} and self.id:
            await self.stop(self.id)
        elif self.cleanup is not None:
            await self.cleanup
