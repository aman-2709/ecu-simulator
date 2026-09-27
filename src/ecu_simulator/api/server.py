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

from aiohttp import WSCloseCode, WSMsgType, web

from ecu_simulator import app
from ecu_simulator.api.options import ApiOptions, ApiStartupError, allowed_hosts
from ecu_simulator.observe import snapshots
from ecu_simulator.observe.connection import Connection
from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.limits import CLOSE_INTERNAL_ERROR, CLOSE_TOO_SLOW, STATE_MIN_INTERVAL_S
from ecu_simulator.observe.publisher import Publisher, TooManyClients
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.observe.writer import NotDelivered, Send, run_writer
from ecu_simulator.transport.socketcan import EndpointConfig

logger = logging.getLogger(__name__)

MAX_BODY = 1024                 # 0010 §4.3: incoming HTTP body
WS_CLOSE_TIMEOUT_S = 2.0        # a stalled client cannot answer the close handshake
WS_WRITER_LIMIT = 64 * 1024     # explicit: the default differs between aiohttp 3.13 and 3.14
WRITER_GRACE_S = 2.0            # a send under way when the socket closes gets this long to resolve
BODY_TIMEOUT_S = 2.0            # a length-less request body must arrive within this, or 408
EVENTS = "/api/v1/events"
INT = re.compile(r"-?[0-9]{1,19}")
# Close codes the server sends when the Publisher, or a dead writer, closed the connection.
CLOSE_REASONS = {CLOSE_TOO_SLOW: b"client too slow", CLOSE_INTERNAL_ERROR: b"internal error"}
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def _query_int(request: web.Request, name: str) -> int | None:
    value = request.query.get(name)
    if value is None:
        return None
    if not INT.fullmatch(value):
        raise web.HTTPBadRequest(text=f"{name} must be an integer")
    return int(value)


def send_via(ws: Any, request: Any) -> Send:
    """``ws.send_str`` with the one known non-delivery made explicit (owner decision 8).

    With compression off, ``send_str`` reaches ``transport.write()`` without suspending, and
    its own pre-write refusals are the two checked here, synchronously. So a refusal here
    means nothing was written (``NotDelivered``); any exception from ``send_str`` itself
    arises at or after the write, and the writer records it as delivery_unknown.
    """
    async def send(text: str) -> None:
        transport = request.transport
        if ws.closed or transport is None or transport.is_closing():
            raise NotDelivered("socket closed or closing: nothing written")
        await ws.send_str(text)
    return send


async def _close_or_abort(ws: web.WebSocketResponse, request: web.Request, code: int, message: bytes) -> None:
    """``ws.close``, bounded. aiohttp's close drains the socket first, with no timeout, so a
    client that stopped reading would hold it forever; after ``WS_CLOSE_TIMEOUT_S`` the socket
    is aborted, which discards its unsent backlog and ends every task waiting on it.

    A close that ends unfinished, by timeout or by cancellation, leaves aiohttp's graceful
    ``transport.close()``, which waits for that backlog; so either way the socket is aborted.
    """
    try:
        await asyncio.wait_for(ws.close(code=code, message=message), WS_CLOSE_TIMEOUT_S)
    except TimeoutError:
        _abort(request)
    except asyncio.CancelledError:
        _abort(request)
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise           # this task was cancelled: pass it on; any other CancelledError ends here


def _abort(request: web.Request) -> None:
    transport = request.transport
    if transport is not None:
        transport.abort()


def _too_large(size: int) -> str:
    return f"Maximum request body size {MAX_BODY} exceeded, actual body size {size} (decisions/0010 §4.3)"


async def _refuse_and_close(request: web.Request, status: int, text: str) -> web.StreamResponse:
    """Answer a request body the server will not take, then close the socket at once.

    ``Connection: close``, and no keep-alive: otherwise aiohttp lingers up to 10 s reading
    the rest of the body before it closes (0010 §4.3). A plain Response: returning an
    HTTPException is deprecated in aiohttp.
    """
    response = web.Response(status=status, text=text)
    response.force_close()
    await response.prepare(request)
    await response.write_eof()
    request.protocol.force_close()      # flushes the response, then closes; skips the lingering read
    return response


async def _body_size(content: Any) -> int:
    """Bytes of a request body, read to its end or to ``MAX_BODY + 1``, whichever is first.

    One ``read(n)`` returns only what has arrived, so a body sent in pieces needs the loop.
    """
    size = 0
    while size <= MAX_BODY:
        chunk = await content.read(MAX_BODY + 1 - size)
        if not chunk:
            break
        size += len(chunk)
    return size


def _log_if_failed(task: asyncio.Task[None]) -> None:
    # The Publisher's tasks run until stop(): one that ends early is a fault, reported at once.
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("%s task failed; the observer API is degraded until restart", task.get_name(), exc_info=error)


class ApiServer:
    def __init__(
        self,
        runtime: app.Runtime,
        endpoints: Iterable[EndpointConfig],
        options: ApiOptions,
        *,
        state_interval_s: float = STATE_MIN_INTERVAL_S,
        connection_options: Mapping[str, Any] | None = None,
        body_timeout_s: float = BODY_TIMEOUT_S,
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
        self._body_timeout_s = body_timeout_s
        self._allowed: frozenset[str] = frozenset()
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._sockets: dict[web.WebSocketResponse, tuple[web.Request, Connection]] = {}
        self._stopping = False
        self._page = resources.files("ecu_simulator.api").joinpath("static/index.html").read_bytes()

    def application(self) -> web.Application:
        @web.middleware
        async def guard(request: web.Request, handler: Handler) -> web.StreamResponse:
            host = request.host.lower()
            if host not in self._allowed:
                raise web.HTTPMisdirectedRequest(text="Host not allowed (decisions/0010 §6)")
            if request.content_length is not None and request.content_length > MAX_BODY:
                return await _refuse_and_close(request, 413, _too_large(request.content_length))
            if request.body_exists and request.content_length is None:
                # Chunked (or otherwise length-less) bodies bypass client_max_size, which
                # only enforces Content-Length: read a bounded probe ourselves (0010 §4.3),
                # to the end or past the limit, within a deadline so a stalled body cannot hold it.
                try:
                    size = await asyncio.wait_for(_body_size(request.content), self._body_timeout_s)
                except TimeoutError:
                    message = "request body not received in time (decisions/0010 §4.3)"
                    return await _refuse_and_close(request, 408, message)
                if size > MAX_BODY:
                    return await _refuse_and_close(request, 413, _too_large(size))
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
        self._stopping = False
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
            asyncio.create_task(self.publisher.run(), name="observer publisher"),
            asyncio.create_task(self.publisher.run_state(state, self._state_interval_s), name="observer state"),
        ]
        for task in self._tasks:
            task.add_done_callback(_log_if_failed)
        logger.info("observer API on http://%s/", sorted(self._allowed)[0])

    async def stop(self) -> None:
        self._stopping = True          # from here a new client is refused before the upgrade
        closing = []
        for ws, (request, conn) in list(self._sockets.items()):
            self.publisher.disconnect(conn, WSCloseCode.GOING_AWAY)   # the ledger records the code sent
            closing.append(_close_or_abort(ws, request, WSCloseCode.GOING_AWAY, b"server shutdown"))
        await asyncio.gather(*closing)
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
        after = _query_int(request, "after")
        if self._stopping:
            raise web.HTTPServiceUnavailable(text="server shutting down")
        try:
            conn, hello, history = self.publisher.connect(after)
        except TooManyClients:
            raise web.HTTPServiceUnavailable(text="too many clients (decisions/0010 §4.3)") from None
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        ws = web.WebSocketResponse(timeout=WS_CLOSE_TIMEOUT_S, compress=False, max_msg_size=MAX_BODY,
                                   writer_limit=WS_WRITER_LIMIT)
        try:
            await ws.prepare(request)
        except BaseException:
            self.publisher.disconnect(conn, WSCloseCode.ABNORMAL_CLOSURE)   # recorded, never sent
            raise
        self._sockets[ws] = (request, conn)
        writer = asyncio.create_task(run_writer(conn, send_via(ws, request), hello, history))
        writer.add_done_callback(lambda task: self._writer_ended(task, conn, request))
        closer = asyncio.create_task(self._close_when_closed(conn, ws, request))
        try:
            async for msg in ws:
                if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                    self.publisher.disconnect(conn, WSCloseCode.POLICY_VIOLATION)   # the ledger records the code sent
                    await _close_or_abort(ws, request, WSCloseCode.POLICY_VIOLATION, b"v1 accepts no client messages")
                    break
        finally:
            self._sockets.pop(ws, None)
            # The first close code wins: a forced 1013, a 1011, a 1008 or a shutdown 1001 stays as
            # recorded. Otherwise the ws code, or 1006 if none: never a code this server did not send.
            self.publisher.disconnect(conn, ws.close_code or WSCloseCode.ABNORMAL_CLOSURE)
            try:
                await asyncio.wait_for(writer, WRITER_GRACE_S)   # a timeout cancels it: delivery_unknown
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as error:     # raised by send: the writer has already resolved its ledger
                logger.debug("writer for connection %d ended: %r", conn.id, error)
            # Never cancelled: disconnect() has closed conn, so the closer either returns at once
            # or finishes its own bounded close-or-abort (a cancel would leave a graceful close).
            await closer
        return ws

    def _writer_ended(self, task: asyncio.Task[None], conn: Connection, request: web.Request) -> None:
        # A writer that dies while its socket is healthy is a server fault, not a slow client:
        # close that connection with 1011 now, rather than leave it registered until overflow
        # forces a misattributed 1013. A socket already going away is the handler's to record:
        # NotDelivered says so explicitly, and a closing transport says so for anything else.
        if task.cancelled() or (error := task.exception()) is None or conn.closed:
            return
        if isinstance(error, NotDelivered):
            return
        transport = request.transport
        if transport is not None and not transport.is_closing():
            logger.error("writer for connection %d failed; closing it with 1011", conn.id, exc_info=error)
            self.publisher.fail_writer(conn)

    async def _close_when_closed(self, conn: Connection, ws: web.WebSocketResponse, request: web.Request) -> None:
        # Sends the close the Publisher or a dead writer decided: 1013 for a forced disconnect,
        # 1011 for a failed offer or writer (0010 §4.3, §5). The writer may be blocked in
        # send_str on a client that stopped reading, so this cannot wait for it (Review Focus 1).
        await conn.wait_closed()
        reason = CLOSE_REASONS.get(conn.close_code or 0)
        if reason is not None:
            await _close_or_abort(ws, request, conn.close_code or 0, reason)
