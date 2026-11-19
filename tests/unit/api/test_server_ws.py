import asyncio
import gc
import json
import socket
import struct
import time

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

from ecu_simulator.observe import snapshots  # noqa: E402
from ecu_simulator.observe.publisher import Publisher  # noqa: E402
from ecu_simulator.transport import DiagnosticRequest  # noqa: E402
from tests.unit.api.support import build, check_delivery_unknown_allowance, url  # noqa: E402

REQ = DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True)


def origin(server):
    return f"http://127.0.0.1:{server.port}"


async def next_json(ws):
    msg = await asyncio.wait_for(ws.receive(), 2)
    assert msg.type == aiohttp.WSMsgType.TEXT, msg
    return json.loads(msg.data)


async def next_exchange(ws):
    # run_state sets a one-slot `dropped` notice every 250 ms (0010 §4.3); it may come first.
    while (event := await next_json(ws))["type"] != "exchange":
        assert event["type"] == "dropped", event
    return event


async def publish(server, n):
    for _ in range(n):
        server.handler(REQ)
    for _ in range(50):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_hello_state_history_then_live(server, session):
    await publish(server, 2)                                          # seq 1, 2 go to history
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        hello = await next_json(ws)
        assert hello == {"type": "hello", "api": 1, "watermark": 2, "oldest_seq": 1}
        assert await next_json(ws) == json.loads(snapshots.state_message(server.runtime, server.unavailable))
        assert [(await next_json(ws))["seq"] for _ in range(2)] == [1, 2]   # history is sent directly
        await publish(server, 1)
        live = await next_exchange(ws)
        assert (live["type"], live["seq"], live["outcome"]) == ("exchange", 3, "responded")
    assert await wait_until(lambda: server.publisher.stats(issued=0)["closed_totals"]["connections"] == 1)
    assert check_delivery_unknown_allowance(server.publisher.stats(issued=server.handler.issued)) == {}


@pytest.mark.asyncio
async def test_the_state_message_carries_the_unavailable_list(server, session):
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        assert (await next_json(ws))["type"] == "hello"
        state = await next_json(ws)
    assert state["type"] == "state"
    assert set(state["vehicle"]) == {"kind", "vin", "signals", "as_of", "unavailable", "nonfinite"}  # §8.2
    assert state["vehicle"]["unavailable"] == ["vehicle.odometer"]
    assert state["vehicle"]["signals"]["vehicle.odometer"] == 0
    assert state["vehicle"]["nonfinite"] == []
    async with session.get(url(server, "/api/v1/vehicle")) as r:
        assert (await r.json())["unavailable"] == state["vehicle"]["unavailable"]


@pytest.mark.asyncio
async def test_state_after_hello_and_a_later_pushed_state_carry_null_and_nonfinite(server, session):
    server.runtime.vehicle.set("engine.coolant_temp", float("nan"))
    server.publisher.push_state(snapshots.state_message(server.runtime, server.unavailable))
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        assert (await next_json(ws))["type"] == "hello"
        state = await next_json(ws)
        assert state["type"] == "state"
        assert state["vehicle"]["signals"]["engine.coolant_temp"] is None
        assert state["vehicle"]["nonfinite"] == ["engine.coolant_temp"]

        server.runtime.vehicle.set("engine.coolant_temp", 91.0)       # recovered
        server.runtime.vehicle.set("engine.intake_temp", float("inf"))
        server.publisher.push_state(snapshots.state_message(server.runtime, server.unavailable))
        pushed = await next_json(ws)
    assert pushed["type"] == "state"
    assert pushed["vehicle"]["signals"]["engine.coolant_temp"] == 91.0
    assert pushed["vehicle"]["signals"]["engine.intake_temp"] is None
    assert pushed["vehicle"]["nonfinite"] == ["engine.intake_temp"]


@pytest.mark.asyncio
async def test_same_value_recovery_reaches_the_client_once(session, monkeypatch):  # M3b §8.3, §12.1
    # The default profile has no scenario: nothing changes the state, so only the
    # first-good publish can send the second state.
    s = build(state_interval_s=0.02)
    assert s.runtime.runner is None
    await s.start()
    try:
        async with session.ws_connect(url(s, "/api/v1/events"), origin=origin(s)) as ws:
            assert (await next_json(ws))["type"] == "hello"
            first = await asyncio.wait_for(ws.receive(), 2)
            assert json.loads(first.data)["type"] == "state"
            original, calls = snapshots.dtcs, []

            def fails_three_times(runtime):
                calls.append(1)
                if len(calls) <= 3:
                    raise RuntimeError("injected")
                return original(runtime)
            monkeypatch.setattr(snapshots, "dtcs", fails_three_times)
            states = []
            deadline = time.monotonic() + 1.0          # 50 attempts at 20 ms: well past the recovery
            while (left := deadline - time.monotonic()) > 0:
                try:
                    msg = await asyncio.wait_for(ws.receive(), left)
                except TimeoutError:
                    break
                assert msg.type == aiohttp.WSMsgType.TEXT, msg
                event = json.loads(msg.data)
                if event["type"] == "state":
                    states.append(msg.data)
                else:
                    assert event["type"] == "dropped", event
            assert s.publisher.state_encode_failed == 3 and len(calls) > 10
            assert states == [first.data]              # one second state, identical, and no further one
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_origin_missing_or_foreign_is_403_before_the_upgrade(server, session):
    for bad in (None, "http://evil.example", f"http://127.0.0.1:{server.port + 1}"):
        with pytest.raises(aiohttp.WSServerHandshakeError) as info:
            await session.ws_connect(url(server, "/api/v1/events"), origin=bad)
        assert info.value.status == 403, bad
    assert server.publisher.stats(issued=0)["connections_opened"] == 0


@pytest.mark.asyncio
async def test_origin_must_match_the_host_of_the_same_request(server, session):
    port = server.port
    events = url(server, "/api/v1/events")
    for host, good, bad in ((f"127.0.0.1:{port}", f"http://127.0.0.1:{port}", f"http://localhost:{port}"),
                            (f"localhost:{port}", f"http://localhost:{port}", f"http://127.0.0.1:{port}")):
        with pytest.raises(aiohttp.WSServerHandshakeError) as info:     # the other loopback name: refused
            await session.ws_connect(events, origin=bad, headers={"Host": host})
        assert info.value.status == 403, (host, bad)
        async with session.ws_connect(events, origin=good, headers={"Host": host}) as ws:   # its own origin: accepted
            assert (await next_json(ws))["type"] == "hello"
    with pytest.raises(aiohttp.WSServerHandshakeError) as info:
        await session.ws_connect(events, origin=f"https://127.0.0.1:{port}")           # scheme is part of the origin
    assert info.value.status == 403


@pytest.mark.asyncio
async def test_a_fifth_client_is_503_before_the_upgrade(server, session):
    sockets = [await session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) for _ in range(4)]
    with pytest.raises(aiohttp.WSServerHandshakeError) as info:
        await session.ws_connect(url(server, "/api/v1/events"), origin=origin(server))
    assert info.value.status == 503 and server.publisher.refused_clients == 1
    for ws in sockets:
        await ws.close()


@pytest.mark.asyncio
async def test_a_bad_after_is_400_before_the_upgrade(server, session):
    for query in ("after=-1", "after=x"):
        with pytest.raises(aiohttp.WSServerHandshakeError) as info:
            await session.ws_connect(url(server, f"/api/v1/events?{query}"), origin=origin(server))
        assert info.value.status == 400, query


@pytest.mark.asyncio
async def test_a_client_data_message_closes_1008(server, session):
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        await next_json(ws)                                           # hello
        assert (await next_json(ws))["type"] == "state"
        await publish(server, 1)
        assert (await next_exchange(ws))["seq"] == 1                  # a live send, received: mark_sent ran
        await ws.send_str("hi")
        while (await asyncio.wait_for(ws.receive(), 2)).type == aiohttp.WSMsgType.TEXT:
            pass
        assert ws.close_code == 1008
    deadline = time.monotonic() + 3                                   # the ledger records the code sent
    while not server.publisher.stats(issued=0)["closed_totals"]["connections"] and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert server.publisher.stats(issued=0)["closed_totals"]["close_codes"] == {"1008": 1}
    assert check_delivery_unknown_allowance(server.publisher.stats(issued=server.handler.issued)) == {}


@pytest.mark.asyncio
async def test_a_client_that_disconnects_mid_stream_is_retired_cleanly(server, session, caplog):  # Review Focus 2
    ws = await session.ws_connect(url(server, "/api/v1/events"), origin=origin(server))
    await next_json(ws)
    await ws.close()
    await publish(server, 200)
    deadline = time.monotonic() + 3
    while server.publisher.stats(issued=server.handler.issued)["clients"] and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    stats = server.publisher.stats(issued=server.handler.issued)
    assert stats["clients"] == 0 and stats["closed_unresolved"] == 0 and stats["closed_totals"]["connections"] == 1
    totals = stats["closed_totals"]
    assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]
    assert "Task exception was never retrieved" not in caplog.text


@pytest.mark.asyncio
async def test_a_stalled_client_is_closed_1013_and_frees_its_slot():  # Review Focus 1
    s = build(connection_options={"max_messages": 4, "overflow_disconnect_s": 0.2})
    await s.start()
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.connect(("127.0.0.1", s.port))
    raw.sendall(
        f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nOrigin: http://127.0.0.1:{s.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n".encode()
    )                                                                 # then never reads again
    try:
        deadline = time.monotonic() + 20
        while s.publisher.forced_disconnects == 0 and time.monotonic() < deadline:
            await publish(s, 1000)                                    # enough to fill every buffer
        assert s.publisher.forced_disconnects == 1
        # clients drops to 0 as soon as the Publisher retires the connection; the totals fold
        # only once the writer's blocked send has resolved (closed_unresolved back to 0).
        while ((stats := s.publisher.stats(issued=s.handler.issued))["clients"] or stats["closed_unresolved"]) \
                and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert stats["clients"] == 0 and stats["closed_unresolved"] == 0
        assert stats["closed_totals"]["close_codes"] == {"1013": 1}
        totals = stats["closed_totals"]
        assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]
        assert check_delivery_unknown_allowance(stats) == {"1013": 1}       # 0010 P5(h): within the allowance
        async with aiohttp.ClientSession() as session:                # the slot is free again
            async with session.ws_connect(url(s, "/api/v1/events"), origin=origin(s)) as ws:
                assert (await next_json(ws))["type"] == "hello"
    finally:
        raw.close()
        await s.stop()


class FakeTransport:
    def __init__(self, closing):
        self.closing = closing

    def is_closing(self):
        return self.closing


class FakeWs:
    def __init__(self, closed=False, error=None):
        self.closed, self.error, self.sent = closed, error, []

    async def send_str(self, text):
        if self.error:
            raise self.error
        self.sent.append(text)


class FakeRequest:
    def __init__(self, closing=False, transport=True):
        self.transport = FakeTransport(closing) if transport else None


@pytest.mark.asyncio
async def test_send_via_raises_not_delivered_only_when_nothing_can_be_written():  # owner decision 8
    from ecu_simulator.api.server import send_via
    from ecu_simulator.observe.writer import NotDelivered
    for ws, request in ((FakeWs(closed=True), FakeRequest()), (FakeWs(), FakeRequest(closing=True)),
                        (FakeWs(), FakeRequest(transport=False))):
        with pytest.raises(NotDelivered):
            await send_via(ws, request)("x")
        assert ws.sent == []                                            # send_str was never called
    ok = FakeWs()
    await send_via(ok, FakeRequest())("x")
    assert ok.sent == ["x"]
    with pytest.raises(ConnectionResetError):                            # after the pre-check: not NotDelivered
        await send_via(FakeWs(error=ConnectionResetError("Connection lost")), FakeRequest())("x")


@pytest.mark.asyncio
async def test_shutdown_closes_clients_with_1001(session):  # Review Focus 5
    s = build()
    await s.start()
    ws = await session.ws_connect(url(s, "/api/v1/events"), origin=origin(s))
    await next_json(ws)
    assert (await next_json(ws))["type"] == "state"
    await publish(s, 1)
    assert (await next_exchange(ws))["seq"] == 1                      # a live send, received: mark_sent ran
    started = time.monotonic()
    await s.stop()
    while (msg := await asyncio.wait_for(ws.receive(), 3)).type == aiohttp.WSMsgType.TEXT:
        pass
    # The code in the close frame the server sent. ws.close_code is not used: the server
    # closes the socket without awaiting the reply (its reader task is busy), and the aiohttp
    # client then overwrites the received 1001 with 1006 when its own reply cannot be written.
    assert (msg.type, msg.data) == (aiohttp.WSMsgType.CLOSE, 1001) and time.monotonic() - started < 5
    assert s.publisher.stats(issued=0)["closed_totals"]["close_codes"] == {"1001": 1}
    assert check_delivery_unknown_allowance(s.publisher.stats(issued=s.handler.issued)) == {}


@pytest.mark.asyncio
async def test_shutdown_is_bounded_with_a_stalled_client():
    # aiohttp's ws.close drains first, with no timeout: without the bound, stop() would wait
    # for a client that never reads (the socket is aborted after WS_CLOSE_TIMEOUT_S instead).
    s = build(connection_options={"max_messages": 4, "overflow_disconnect_s": 60})
    await s.start()
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.connect(("127.0.0.1", s.port))
    raw.sendall(
        f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nOrigin: http://127.0.0.1:{s.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    try:
        deadline = time.monotonic() + 20
        while not (s._sockets and next(iter(s._sockets.values()))[0].protocol.writing_paused) \
                and time.monotonic() < deadline:
            await publish(s, 1000)
        assert s._sockets and next(iter(s._sockets.values()))[0].protocol.writing_paused
        started = time.monotonic()
        await s.stop()
        assert time.monotonic() - started < 5
        stats = s.publisher.stats(issued=s.handler.issued)
        assert stats["clients"] == 0 and stats["closed_unresolved"] == 0
        assert stats["closed_totals"]["close_codes"] == {"1001": 1}
    finally:
        raw.close()
        await s.stop()


@pytest.mark.asyncio
async def test_a_data_frame_during_a_forced_close_still_ends_in_an_abort():
    # A stalled client, forced to 1013 while the close is blocked in aiohttp's drain, then sends
    # one data frame: the 1008 path must not cancel the forced close into a graceful close that
    # waits forever on the backlog; the socket must still be aborted within the bound.
    s = build(connection_options={"max_messages": 4, "overflow_disconnect_s": 0.2})
    await s.start()
    raw = socket.socket()
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    raw.connect(("127.0.0.1", s.port))
    raw.sendall(
        f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nOrigin: http://127.0.0.1:{s.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    try:
        deadline = time.monotonic() + 20
        while not s._sockets and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        request = next(iter(s._sockets.values()))[0]
        while s.publisher.forced_disconnects == 0 and time.monotonic() < deadline:
            await publish(s, 1000)
        assert s.publisher.forced_disconnects == 1
        mask = b"\x01\x02\x03\x04"
        raw.sendall(b"\x81\x82" + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(b"hi")))   # masked "hi"
        gone = time.monotonic() + 5
        while request.transport is not None and time.monotonic() < gone:
            await asyncio.sleep(0.05)
        assert request.transport is None                                  # aborted, not left closing
        raw.settimeout(5)                                                 # a timeout here fails the test
        try:
            while raw.recv(65536):                                        # EOF ...
                pass
        except ConnectionResetError:                                      # ... or a reset: either way, gone
            pass
        stats = s.publisher.stats(issued=s.handler.issued)
        assert stats["closed_unresolved"] == 0 and stats["closed_totals"]["close_codes"] == {"1013": 1}
    finally:
        raw.close()
        await s.stop()


async def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    return condition()


async def closing_frame(ws):
    # The close frame the server sent; ws.close_code may be overwritten by a failed reply (above).
    while (msg := await asyncio.wait_for(ws.receive(), 5)).type == aiohttp.WSMsgType.TEXT:
        pass
    return msg.type, msg.data


@pytest.mark.asyncio
async def test_a_connection_the_publisher_abandons_is_closed_1011(server, session):  # 0010 §5
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        await next_json(ws)
        (conn,) = server.publisher.connections

        def offer(text):
            raise RuntimeError("offer failed")
        conn.offer = offer
        await publish(server, 1)
        assert await closing_frame(ws) == (aiohttp.WSMsgType.CLOSE, 1011)
    assert await wait_until(lambda: not server._sockets)
    stats = server.publisher.stats(issued=server.handler.issued)
    assert stats["fanout_failed"] == 1 and stats["closed_totals"]["close_codes"] == {"1011": 1}
    assert stats["writer_failed"] == 0


@pytest.mark.asyncio
async def test_a_writer_that_dies_on_an_open_socket_closes_it_1011(server, session, monkeypatch):
    from ecu_simulator.api import server as server_module
    real = server_module.send_via

    def failing_send_via(ws, request):
        send = real(ws, request)

        async def wrapped(text):
            if '"type":"exchange"' in text:
                raise RuntimeError("writer bug")        # not NotDelivered, and the socket is healthy
            await send(text)
        return wrapped
    monkeypatch.setattr(server_module, "send_via", failing_send_via)
    async with session.ws_connect(url(server, "/api/v1/events"), origin=origin(server)) as ws:
        await next_json(ws)
        await publish(server, 1)
        assert await closing_frame(ws) == (aiohttp.WSMsgType.CLOSE, 1011)
    assert await wait_until(lambda: not server._sockets)
    stats = server.publisher.stats(issued=server.handler.issued)
    assert stats["clients"] == 0 and stats["closed_totals"]["close_codes"] == {"1011": 1}
    assert stats["closed_totals"]["delivery_unknown"] == 1              # owner decision 8: any other exception
    assert (stats["writer_failed"], stats["fanout_failed"]) == (1, 0)


def raw_upgrade(s, rcvbuf=None):
    raw = socket.socket()
    if rcvbuf:
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    raw.connect(("127.0.0.1", s.port))
    raw.sendall(
        f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nOrigin: http://127.0.0.1:{s.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    return raw


def reset(raw):
    raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))   # close sends RST
    raw.close()


class LoopErrors:
    """Records every call of the event loop's exception handler (Review Focus 2)."""

    def __enter__(self):
        self.loop = asyncio.get_running_loop()
        self.previous = self.loop.get_exception_handler()
        self.calls = []
        self.loop.set_exception_handler(lambda loop, context: self.calls.append(context))
        return self

    def __exit__(self, *exc):
        gc.collect()                  # "Task exception was never retrieved" is reported on collection
        self.loop.set_exception_handler(self.previous)


async def closed_totals_after_reset(s):
    assert await wait_until(lambda: not s._sockets and not s.publisher.stats(issued=0)["closed_unresolved"])
    stats = s.publisher.stats(issued=s.handler.issued)
    totals = stats["closed_totals"]
    assert stats["clients"] == 0 and stats["closed_unresolved"] == 0 and totals["close_codes"] == {"1006": 1}
    assert totals["enqueued"] == totals["sent"] + totals["delivery_unknown"] + totals["discarded_on_close"]
    return totals


@pytest.mark.asyncio
async def test_a_client_reset_during_the_live_stream_is_retired_cleanly():  # Review Focus 2
    with LoopErrors() as errors:
        s = build()
        await s.start()
        try:
            raw = raw_upgrade(s)
            raw.setblocking(False)
            receive = asyncio.get_running_loop().sock_recv
            assert (await asyncio.wait_for(receive(raw, 65536), 2)).startswith(b"HTTP/1.1 101")
            await publish(s, 20)
            assert await asyncio.wait_for(receive(raw, 65536), 2)          # the live stream is flowing
            reset(raw)
            await publish(s, 20)                                          # sends after the reset: NotDelivered
            totals = await closed_totals_after_reset(s)
            # Not backpressured, a send never suspends: nothing is in flight across the reset,
            # so nothing is delivery_unknown (owner decision 8).
            assert totals["delivery_unknown"] == 0
            assert check_delivery_unknown_allowance(s.publisher.stats(issued=s.handler.issued)) == {}
        finally:
            await s.stop()
    assert errors.calls == []


@pytest.mark.asyncio
async def test_a_client_reset_while_backpressured_is_delivery_unknown_once():  # Review Focus 2
    with LoopErrors() as errors:
        s = build(connection_options={"max_messages": 4, "overflow_disconnect_s": 60})
        await s.start()
        try:
            raw = raw_upgrade(s, rcvbuf=4096)                             # then never reads
            assert await wait_until(lambda: bool(s._sockets))
            request = next(iter(s._sockets.values()))[0]
            deadline = time.monotonic() + 20

            def writer_blocked():
                # A full queue behind one unresolved exchange: the writer is stuck in send_str.
                (ledger,) = s.publisher.stats(issued=0)["connections"]
                return request.protocol.writing_paused and ledger["queued"] == 4 + 1
            while not writer_blocked() and time.monotonic() < deadline:
                await publish(s, 1000)
            assert writer_blocked()
            reset(raw)
            totals = await closed_totals_after_reset(s)
            # The blocked send ends in ConnectionError('Connection lost'): any exception other
            # than NotDelivered is delivery_unknown (owner decision 8).
            assert totals["delivery_unknown"] == 1
            assert check_delivery_unknown_allowance(s.publisher.stats(issued=s.handler.issued)) == {"1006": 1}
        finally:
            await s.stop()
    assert errors.calls == []


@pytest.mark.asyncio
async def test_a_publisher_task_that_fails_is_logged_at_once(caplog, monkeypatch):
    s = build()
    # A failing snapshot is contained by run_state (M3b §8.3), so the fault is one it does not catch.
    monkeypatch.setattr(Publisher, "push_dropped", lambda self: 1 / 0)
    await s.start()
    try:
        assert await wait_until(lambda: any(
            r.name == "ecu_simulator.api.server" and r.levelname == "ERROR" and r.exc_info
            and isinstance(r.exc_info[1], ZeroDivisionError) for r in caplog.records), timeout=2)
    finally:
        await s.stop()
    assert not [r for r in caplog.records if "never retrieved" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_client_connecting_during_stop_is_refused_503(session):
    # stop() spends up to WS_CLOSE_TIMEOUT_S on a stalled client while still listening: a new
    # client in that window must be refused before the upgrade, never recorded as sent a 1001.
    s = build(connection_options={"max_messages": 4, "overflow_disconnect_s": 60})
    await s.start()
    raw = raw_upgrade(s, rcvbuf=4096)
    try:
        assert await wait_until(lambda: bool(s._sockets))
        request = next(iter(s._sockets.values()))[0]
        deadline = time.monotonic() + 20
        while not request.protocol.writing_paused and time.monotonic() < deadline:
            await publish(s, 1000)
        stopping = asyncio.create_task(s.stop())
        await asyncio.sleep(0.2)                                          # stop() is closing the stalled client
        assert not stopping.done()
        with pytest.raises(aiohttp.WSServerHandshakeError) as info:
            await session.ws_connect(url(s, "/api/v1/events"), origin=origin(s))
        assert info.value.status == 503
        await stopping
        stats = s.publisher.stats(issued=s.handler.issued)
        assert stats["connections_opened"] == 1 and stats["closed_totals"]["close_codes"] == {"1001": 1}
    finally:
        raw.close()
        await s.stop()


@pytest.mark.asyncio
async def test_a_writer_ended_by_not_delivered_is_never_a_writer_failure():
    # NotDelivered means the socket was closed or closing: the handler records that close,
    # whatever the transport looks like by the time the done-callback runs.
    from ecu_simulator.observe.writer import NotDelivered
    s = build()
    conn, _, _ = s.publisher.connect()
    ended = asyncio.get_running_loop().create_future()
    ended.set_exception(NotDelivered("nothing written"))
    s._writer_ended(ended, conn, FakeRequest())                        # a healthy-looking transport
    assert not conn.closed and s.publisher.writer_failed == 0


@pytest.mark.asyncio
async def test_a_forced_stalled_connection_carries_at_most_one_reported_delivery_unknown(session):
    # The accepted 0010 P5(h) contract: a connection closed 1013 may carry at most one
    # delivery_unknown, reported under "1013"; every other connection carries none.
    s = build(connection_options={"overflow_disconnect_s": 0.2})
    await s.start()
    raw = raw_upgrade(s, rcvbuf=4096)                                    # stalled: never reads
    healthy = await session.ws_connect(url(s, "/api/v1/events"), origin=origin(s))
    received = 0

    async def read():
        nonlocal received
        async for msg in healthy:
            received += json.loads(msg.data)["type"] == "exchange"
    reading = asyncio.create_task(read())
    try:
        deadline = time.monotonic() + 20
        while s.publisher.forced_disconnects == 0 and time.monotonic() < deadline:
            await publish(s, 1000)
        assert s.publisher.forced_disconnects == 1
        assert await wait_until(lambda: not s.publisher.stats(issued=0)["closed_unresolved"])
        (open_healthy,) = s.publisher.stats(issued=0)["connections"]

        def drained():                                                  # every resolved send has arrived
            (ledger,) = s.publisher.stats(issued=0)["connections"]
            return ledger["queued"] == 0 and received == ledger["sent"] + ledger["delivery_unknown"]
        assert await wait_until(drained)
        await healthy.close()                                           # nothing in flight: a clean 1000
        await reading
        stats = s.publisher.stats(issued=s.handler.issued)
        by_id = {ledger["id"]: ledger for ledger in stats["closed_connections"]}
        assert by_id[open_healthy["id"]]["delivery_unknown"] == 0 and received > 0      # the healthy client
        stalled = next(ledger for ledger in by_id.values() if ledger["close_code"] == 1013)
        assert stalled["delivery_unknown"] <= 1 and stalled["delivery_unknown"] == 1     # the bound; today's value
        totals = stats["closed_totals"]
        assert totals["delivery_unknown_by_close_code"] == {"1013": 1}
        assert totals["delivery_unknown_by_close_code"]["1013"] <= totals["close_codes"]["1013"]
        assert stats["closed_unresolved"] == 0
        assert check_delivery_unknown_allowance(stats) == {"1013": 1}
    finally:
        reading.cancel()
        raw.close()
        await s.stop()
