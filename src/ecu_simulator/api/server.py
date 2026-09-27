"""The observer API server (decisions/0010 §5, §6). The only module that imports aiohttp.

It owns the observer: HandOff, Publisher and the ObservedDispatcher that run() hands the
transport. The initial state is computed here, before the socket is bound, so the first
WebSocket client receives it (0010 §4.5).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from importlib import resources
from typing import Any

from aiohttp import WSCloseCode, web

from ecu_simulator import app
from ecu_simulator.api.options import ApiOptions, ApiStartupError, allowed_hosts
from ecu_simulator.observe import snapshots
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.limits import STATE_MIN_INTERVAL_S
from ecu_simulator.observe.publisher import Publisher
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport.socketcan import EndpointConfig

logger = logging.getLogger(__name__)

MAX_BODY = 1024                 # 0010 §4.3: incoming HTTP body
WS_CLOSE_TIMEOUT_S = 2.0        # a stalled client cannot answer the close handshake
WS_WRITER_LIMIT = 64 * 1024     # explicit: the default differs between aiohttp 3.13 and 3.14
WRITER_GRACE_S = 2.0            # a send under way when the socket closes gets this long to resolve
EVENTS = "/api/v1/events"
INT = re.compile(r"-?[0-9]{1,19}")
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _query_int(request: web.Request, name: str) -> int | None:
    value = request.query.get(name)
    if value is None:
        return None
    if not INT.fullmatch(value):
        raise web.HTTPBadRequest(text=f"{name} must be an integer")
    return int(value)


class ApiServer:
    def __init__(
        self,
        runtime: app.Runtime,
        endpoints: Iterable[EndpointConfig],
        options: ApiOptions,
        *,
        state_interval_s: float = STATE_MIN_INTERVAL_S,
        connection_options: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            snapshots.check_state_size(runtime)
        except ValueError as error:
            raise ApiStartupError(str(error)) from error
        self.runtime = runtime
        self.options = options
        self.handoff = HandOff()
        self.publisher = Publisher(self.handoff, runtime.router, {e.name: e for e in endpoints},
                                   connection_options=connection_options)
        self.handler = ObservedDispatcher(runtime.dispatcher, self.handoff, self.publisher.wake)
        # Owner decision 2026-09-27: state exists before the first client can connect.
        self.publisher.push_state(snapshots.state_message(runtime))
        self.started_at = time.time()
        self.port: int | None = None
        self._state_interval_s = state_interval_s
        self._allowed: frozenset[str] = frozenset()
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._sockets: set[web.WebSocketResponse] = set()
        self._page = resources.files("ecu_simulator.api").joinpath("static/index.html").read_bytes()

    def application(self) -> web.Application:
        @web.middleware
        async def guard(request: web.Request, handler: Handler) -> web.StreamResponse:
            host = request.host.lower()
            if host not in self._allowed:
                raise web.HTTPMisdirectedRequest(text="Host not allowed (decisions/0010 §6)")
            if request.content_length is not None and request.content_length > MAX_BODY:
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=request.content_length)
            upgrade = request.path == EVENTS and request.method == "GET"
            # Same-origin against THIS request's validated Host: localhost and 127.0.0.1 are
            # different origins and are never substituted for one another (owner, 2026-09-27).
            if upgrade and (request.headers.get("Origin") or "").lower() != f"http://{host}":
                raise web.HTTPForbidden(text="Origin must match Host (decisions/0010 §6)")
            return await handler(request)

        application = web.Application(middlewares=[guard], client_max_size=MAX_BODY)
        routes: list[tuple[str, Handler]] = [
            ("/", self._index),
            ("/api/v1/status", self._status),
            ("/api/v1/vehicle", self._vehicle),
            ("/api/v1/dtcs", self._dtcs),
            ("/api/v1/ecus", self._ecus),
            ("/api/v1/exchanges", self._exchanges),
            (EVENTS, self._events),
        ]
        for path, handler in routes:
            application.router.add_get(path, handler, allow_head=False)
        return application

    async def start(self) -> None:
        self._runner = web.AppRunner(self.application(), access_log=None, shutdown_timeout=2.0)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, self.options.host, self.options.port).start()
        except OSError as error:
            await self._runner.cleanup()
            self._runner = None
            raise ApiStartupError(
                f"--api cannot listen on {self.options.host}:{self.options.port}: {error.strerror}"
            ) from error
        self.port = int(self._runner.addresses[0][1])
        self._allowed = allowed_hosts(self.options.host, self.port)
        state = lambda: snapshots.state_message(self.runtime)  # noqa: E731
        self._tasks = [
            asyncio.create_task(self.publisher.run()),
            asyncio.create_task(self.publisher.run_state(state, self._state_interval_s)),
        ]
        logger.info("observer API on http://%s/", sorted(self._allowed)[0])

    async def stop(self) -> None:
        for ws in list(self._sockets):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"server shutdown")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(body=self._page, content_type="text/html", charset="utf-8")

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.status(
            self.runtime, self.publisher, self.handler.issued, self.started_at,
            self.options.version, self.options.profile,
        ))

    async def _vehicle(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.vehicle(self.runtime))

    async def _dtcs(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.dtcs(self.runtime))

    async def _ecus(self, request: web.Request) -> web.Response:
        return web.json_response(snapshots.ecus(self.runtime))

    async def _exchanges(self, request: web.Request) -> web.Response:
        after, limit = _query_int(request, "after"), _query_int(request, "limit")
        history = self.publisher.history
        try:
            texts, gap = history.since(after, limit)
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        oldest = "null" if history.oldest_seq is None else str(history.oldest_seq)
        body = (f'{{"watermark":{history.last_seq},"oldest_seq":{oldest},'
                f'"gap":{"true" if gap else "false"},"events":[{",".join(texts)}]}}')
        return web.Response(text=body, content_type="application/json")

    async def _events(self, request: web.Request) -> web.StreamResponse:
        raise web.HTTPNotImplemented(text="Task 5")
