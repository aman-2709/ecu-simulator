import asyncio
import json
import socket
import time

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

from ecu_simulator.api.options import ApiStartupError  # noqa: E402
from ecu_simulator.observe import snapshots  # noqa: E402
from tests.unit.api.support import build, raw_request, url  # noqa: E402

ROUTES = ["/api/v1/status", "/api/v1/vehicle", "/api/v1/dtcs", "/api/v1/ecus", "/api/v1/exchanges"]


@pytest.mark.asyncio
async def test_every_route_answers_json(server, session):
    for path in ROUTES:
        async with session.get(url(server, path)) as r:
            assert r.status == 200 and r.content_type == "application/json", path
            body = await r.json()
    async with session.get(url(server, "/api/v1/status")) as r:
        status = await r.json()
    assert status["profile"] == "profiles/ice_default.yaml" and status["api"]["issued_seq"] == 0
    assert body == {"watermark": 0, "oldest_seq": None, "gap": False, "events": []}


def test_the_initial_state_is_ready_before_the_server_starts():
    s = build()                                           # constructed, not started: no socket, no task
    conn, _, _ = s.publisher.connect()
    assert conn.take_state() == snapshots.state_message(s.runtime, s.unavailable)


@pytest.mark.asyncio
async def test_vehicle_carries_the_unavailable_list(server, session):
    # 0010 §5, ninth revision: one added key; signals still carries the stored value.
    async with session.get(url(server, "/api/v1/vehicle")) as r:
        body = await r.json()
    assert set(body) == {"kind", "vin", "signals", "as_of", "unavailable", "nonfinite"}  # §8.2
    assert body["unavailable"] == ["vehicle.odometer"]
    assert body["signals"]["vehicle.odometer"] == 0
    assert body["nonfinite"] == []


@pytest.mark.asyncio
async def test_vehicle_with_a_nonfinite_signal_answers_200_null_and_nonfinite(server, session):
    server.runtime.vehicle.set("engine.coolant_temp", float("nan"))
    async with session.get(url(server, "/api/v1/vehicle")) as r:
        assert r.status == 200
        body = await r.json()
    assert body["signals"]["engine.coolant_temp"] is None
    assert body["nonfinite"] == ["engine.coolant_temp"]


@pytest.mark.asyncio
async def test_the_unavailable_list_is_computed_once_at_construction(session, monkeypatch):
    from ecu_simulator.observe import availability

    calls = []
    original = availability.unavailable
    monkeypatch.setattr(availability, "unavailable", lambda profile: calls.append(1) or original(profile))
    s = build()
    assert calls == [1]
    await s.start()
    try:
        for _ in range(3):
            async with session.get(url(s, "/api/v1/vehicle")) as r:
                assert (await r.json())["unavailable"] == ["vehicle.odometer"]
            snapshots.state_message(s.runtime, s.unavailable)
    finally:
        await s.stop()
    assert calls == [1]


@pytest.mark.asyncio
async def test_exchanges_reflect_dispatched_requests(server, session):
    from ecu_simulator.transport import DiagnosticRequest
    server.handler(DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True))
    server.publisher.drain_turn()
    async with session.get(url(server, "/api/v1/exchanges?after=0&limit=10")) as r:
        body = await r.json()
    assert (body["watermark"], body["gap"], [e["request"] for e in body["events"]]) == (1, False, ["010c"])


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=0", "limit=x", "limit=1.5", "after=-1", "after=", "after=1e3"])
async def test_bad_exchange_queries_are_400(server, session, query):
    async with session.get(url(server, f"/api/v1/exchanges?{query}")) as r:
        assert r.status == 400


@pytest.mark.asyncio
async def test_every_other_method_is_405(server, session):
    for path in ROUTES + ["/", "/app.css", "/app.js", "/api/v1/events"]:
        for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            async with session.request(method, url(server, path)) as r:
                assert r.status == 405, (method, path)


@pytest.mark.asyncio
async def test_host_header_rules(server, session):  # Review Focus 3
    port = server.port
    for host in (f"localhost:{port}", f"LOCALHOST:{port}", f"127.0.0.1:{port}"):
        async with session.get(url(server, "/api/v1/status"), headers={"Host": host}) as r:
            assert r.status == 200, host
    for host in ("127.0.0.1", f"127.0.0.1:{port + 1}", f"[::1]:{port}", f"evil.example:{port}"):
        async with session.get(url(server, "/api/v1/status"), headers={"Host": host}) as r:
            assert r.status == 421, host
    reply = await raw_request(port, b"GET /api/v1/status HTTP/1.0\r\n\r\n")   # no Host at all
    assert reply.startswith(b"HTTP/1.0 421") or reply.startswith(b"HTTP/1.1 421"), reply[:40]


@pytest.mark.asyncio
async def test_no_response_carries_cors_headers(server, session):
    async with session.get(url(server, "/api/v1/status"), headers={"Origin": "http://evil.example"}) as r:
        assert not [h for h in r.headers if h.lower().startswith("access-control-")]


@pytest.mark.asyncio
async def test_a_body_over_1_kib_is_413(server, session):
    async with session.get(url(server, "/api/v1/status"), data=b"x" * 1025) as r:
        assert r.status == 413


@pytest.mark.asyncio
async def test_a_chunked_body_over_1_kib_is_413(server):
    port = server.port
    request = (
        f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n800\r\n"
    ).encode() + b"x" * 2048 + b"\r\n0\r\n\r\n"
    reply = await raw_request(port, request)
    assert reply.startswith(b"HTTP/1.1 413"), reply[:40]


@pytest.mark.asyncio
async def test_a_small_chunked_body_is_accepted(server):
    port = server.port
    request = (
        f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\na\r\n"
    ).encode() + b"x" * 10 + b"\r\n0\r\n\r\n"
    reply = await raw_request(port, request)
    assert reply.startswith(b"HTTP/1.1 200"), reply[:40]


@pytest.mark.asyncio
async def test_a_busy_port_is_an_api_startup_error():
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        s = build()
        s.options = type(s.options)("127.0.0.1", holder.getsockname()[1], "p", "t")
        with pytest.raises(ApiStartupError, match="cannot listen"):
            await s.start()
    finally:
        holder.close()


def test_an_oversized_state_refuses_to_construct(monkeypatch):
    monkeypatch.setattr(snapshots, "STATE_MAX_BYTES", 10)
    with pytest.raises(ApiStartupError, match="256 KiB"):
        build()


async def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


async def get_status(session, server):
    async with session.get(url(server, "/api/v1/status")) as r:
        assert r.status == 200
        return (await r.json())["api"]


@pytest.mark.asyncio
async def test_status_carries_the_encoding_health_fields(server, session):  # M3b §8.3
    api = await get_status(session, server)
    assert api["state_encode_failed"] == 0 and api["vehicle_encode_failed"] == 0
    assert set(api["state_encoding"]) == {"ok", "last_ok_at", "last_failed_at"}
    assert api["state_encoding"]["ok"] is True and api["state_encoding"]["last_failed_at"] is None


def test_the_initial_push_sets_ok_and_last_ok_at():  # M3b §8.3: no task has run yet
    s = build()
    assert s.publisher.state_encoding["ok"] is True
    assert s.publisher.state_encoding["last_ok_at"] is not None


@pytest.mark.asyncio
async def test_health_is_for_the_full_state_not_for_get_vehicle(server, session, monkeypatch):  # M3b §8.3
    def broken_dtcs(runtime):
        raise RuntimeError("injected DTC failure")
    monkeypatch.setattr(snapshots, "dtcs", broken_dtcs)
    assert await wait_until(lambda: server.publisher.state_encode_failed >= 1)
    first = await get_status(session, server)
    assert first["state_encoding"]["ok"] is False and first["state_encoding"]["last_failed_at"] is not None
    count = first["state_encode_failed"]
    assert await wait_until(lambda: server.publisher.state_encode_failed > count)
    assert (await get_status(session, server))["state_encode_failed"] > count     # still rising
    async with session.get(url(server, "/api/v1/vehicle")) as r:
        assert r.status == 200                                       # the vehicle part is fine
    after = await get_status(session, server)
    assert after["state_encoding"]["ok"] is False                    # and did not report the full state healthy
    assert after["vehicle_encode_failed"] == 0


@pytest.mark.asyncio
async def test_a_residual_vehicle_encode_failure_is_500_and_counted(session, monkeypatch):  # M3b §8.3
    # A long state interval: the state task makes its first attempt at start, then none
    # during the test, so state_encoding can only change through GET /vehicle.
    s = build(state_interval_s=60)
    t0 = s.publisher.state_encoding["last_ok_at"]           # set by the initial push
    await s.start()
    try:
        assert await wait_until(lambda: s.publisher.state_encoding["last_ok_at"] != t0)   # the first attempt ran
        before = await get_status(session, s)
        original = snapshots.vehicle

        def as_of_nan(runtime, unavailable):
            return {**original(runtime, unavailable), "as_of": float("nan")}   # past the sanitiser
        monkeypatch.setattr(snapshots, "vehicle", as_of_nan)
        for n in (1, 2):
            async with session.get(url(s, "/api/v1/vehicle")) as r:
                assert r.status == 500
                assert await r.text() == "vehicle state could not be encoded (decisions/0010 §4.3)"
            after = await get_status(session, s)                     # /status is still 200
            assert after["vehicle_encode_failed"] == n
            assert after["state_encoding"] == before["state_encoding"]
            assert after["state_encode_failed"] == before["state_encode_failed"]
    finally:
        await s.stop()


def _raising_parse_constant(token):
    raise ValueError(f"unexpected constant: {token}")


@pytest.mark.asyncio
async def test_status_is_valid_json_while_the_state_task_fails_on_a_nonfinite_as_of(session):  # M3b §8.2/§8.3
    s = build(profile="ice_scenario.yaml")
    await s.start()
    try:
        s.runtime.runner._last_applied = float("inf")       # test-only, as Task 26/27 tests already do
        assert await wait_until(lambda: s.publisher.state_encode_failed >= 1)
        async with session.get(url(s, "/api/v1/status")) as r:
            assert r.status == 200
            body = await r.text()                            # raw text: r.json() accepts NaN, too lenient here
        parsed = json.loads(body, parse_constant=_raising_parse_constant)
        assert parsed["scenario"]["t_last_applied"] is None
        assert parsed["api"]["state_encoding"]["ok"] is False
    finally:
        await s.stop()


def test_a_nonfinite_value_at_startup_starts_sanitised_and_ok():  # M3b §8.3
    s = build(prepare=lambda runtime: runtime.vehicle.set("engine.coolant_temp", float("inf")))
    conn, _, _ = s.publisher.connect()
    state = json.loads(conn.take_state())
    assert state["vehicle"]["signals"]["engine.coolant_temp"] is None
    assert state["vehicle"]["nonfinite"] == ["engine.coolant_temp"]
    assert s.publisher.state_encoding["ok"] is True


def dtcs_with_a_nan(runtime):
    return {"engine": {"codes": [], "mil": False, "extra": float("nan")}}   # only the guard catches it


def dtcs_raising(runtime):
    raise RuntimeError("injected")


@pytest.mark.parametrize("fault, name", [(dtcs_with_a_nan, "ValueError"), (dtcs_raising, "RuntimeError")])
def test_a_residual_startup_failure_is_an_api_startup_error_naming_its_type(monkeypatch, fault, name):
    monkeypatch.setattr(snapshots, "dtcs", fault)
    with pytest.raises(ApiStartupError, match=name) as caught:
        build()
    assert "256 KiB" not in str(caught.value)                        # not reported as the size rule


@pytest.mark.asyncio
async def test_a_body_stalled_mid_chunk_is_408(session):  # 0010 §4.3
    s = build(body_timeout_s=0.2)
    await s.start()
    try:
        request = (
            f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\n"
            "Transfer-Encoding: chunked\r\n\r\n800\r\n"
        ).encode() + b"x" * 100                                            # then silence
        reply = await asyncio.wait_for(raw_request(s.port, request), 2)
        assert reply.startswith(b"HTTP/1.1 408"), reply[:40]
        async with session.get(url(s, "/api/v1/status")) as r:            # the server is not wedged
            assert r.status == 200
    finally:
        await s.stop()


@pytest.mark.asyncio
async def test_a_bad_host_with_a_chunked_body_is_421_without_reading_it(server):
    # The body is announced and never sent: the 421 must not wait for it, nor for the
    # body timeout (2 s here, so a 408 would mean the body was waited for).
    request = (
        f"GET /api/v1/status HTTP/1.1\r\nHost: evil.example:{server.port}\r\n"
        "Transfer-Encoding: chunked\r\n\r\n800\r\n"
    ).encode()
    started = time.monotonic()
    reply = await asyncio.wait_for(raw_request(server.port, request), 2)
    assert reply.startswith(b"HTTP/1.1 421"), reply[:40]
    assert time.monotonic() - started < 1


@pytest.mark.asyncio
async def test_a_chunked_body_over_1_kib_sent_in_pieces_is_413(server):
    # One read returns only what has arrived: the 1 KiB check must read on until it knows.
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write((f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
                  "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n800\r\n").encode() + b"x" * 100)
    await writer.drain()
    await asyncio.sleep(0.1)
    writer.write(b"x" * 1948 + b"\r\n0\r\n\r\n")
    await writer.drain()
    reply = await asyncio.wait_for(reader.read(65536), 2)
    writer.close()
    await writer.wait_closed()
    assert reply.startswith(b"HTTP/1.1 413"), reply[:40]


def chunked_head(port: int, keep_alive: bool = True) -> bytes:
    return (f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nTransfer-Encoding: chunked\r\n"
            + ("" if keep_alive else "Connection: close\r\n") + "\r\n").encode()


@pytest.mark.asyncio
async def test_a_slow_drip_body_is_408_at_the_deadline():
    # One byte every 0.1 s keeps every single read short: only a deadline over the whole
    # body read, not one per read, answers before the drip's natural end (3 s here).
    s = build(body_timeout_s=0.5)
    await s.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", s.port)

    async def drip():
        writer.write(chunked_head(s.port))
        for _ in range(30):
            writer.write(b"1\r\nx\r\n")
            await writer.drain()
            await asyncio.sleep(0.1)
    dripping = asyncio.create_task(drip())
    try:
        started = time.monotonic()
        reply = await asyncio.wait_for(reader.read(65536), 5)
        elapsed = time.monotonic() - started
        assert reply.startswith(b"HTTP/1.1 408"), reply[:40]
        assert 0.5 <= elapsed < 1.5, elapsed
    finally:
        dripping.cancel()
        writer.close()
        await s.stop()


@pytest.mark.asyncio
async def test_a_body_over_1_kib_in_40_chunks_of_64_bytes_is_413(server):
    # A raw socket: the server answers past 1 KiB and closes, so the later chunks may meet a
    # reset; the 413 is still in the receive buffer (asyncio streams would hide it).
    loop = asyncio.get_running_loop()
    raw = socket.socket()
    raw.setblocking(False)
    try:
        await loop.sock_connect(raw, ("127.0.0.1", server.port))
        try:
            await loop.sock_sendall(raw, chunked_head(server.port, keep_alive=False))
            for _ in range(40):                                          # 2560 bytes, one chunk per write
                await loop.sock_sendall(raw, b"40\r\n" + b"x" * 64 + b"\r\n")
                await asyncio.sleep(0.005)
            await loop.sock_sendall(raw, b"0\r\n\r\n")
        except (ConnectionResetError, BrokenPipeError):
            pass                                                         # answered past 1 KiB and closed
        reply = await asyncio.wait_for(loop.sock_recv(raw, 65536), 2)
        assert reply.startswith(b"HTTP/1.1 413"), reply[:40]
    finally:
        raw.close()


@pytest.mark.asyncio
async def test_20_concurrent_stalled_bodies_do_not_wedge_the_server(session):
    s = build(body_timeout_s=0.5)
    await s.start()
    stalled = []
    try:
        for _ in range(20):
            reader, writer = await asyncio.open_connection("127.0.0.1", s.port)
            writer.write(chunked_head(s.port) + b"800\r\n" + b"x" * 10)  # then silence
            await writer.drain()
            stalled.append((reader, writer))
        started = time.monotonic()
        async with session.get(url(s, "/api/v1/status")) as r:          # answered while all 20 wait
            assert r.status == 200
        assert time.monotonic() - started < 0.4
        replies = await asyncio.wait_for(asyncio.gather(*(r.read(65536) for r, _ in stalled)), 3)
        assert all(reply.startswith(b"HTTP/1.1 408") for reply in replies), [r[:20] for r in replies]
    finally:
        for _, writer in stalled:
            writer.close()
        await s.stop()


async def reply_then_eof(reader) -> tuple[bytes, float]:
    """The response, then the seconds from its arrival until the server closed the socket."""
    reply = await asyncio.wait_for(reader.read(65536), 3)
    arrived = time.monotonic()
    try:
        while await asyncio.wait_for(reader.read(65536), 3):
            pass
    except ConnectionResetError:
        pass
    return reply, time.monotonic() - arrived


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["408 stalled chunked", "413 oversized chunked", "413 oversized length"])
async def test_a_refused_body_is_answered_with_connection_close_and_the_socket_closed(case):
    # No keep-alive after a body the server will not take: without it aiohttp lingers up to
    # 10 s reading the rest (0010 §4.3).
    s = build(body_timeout_s=0.3)
    await s.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", s.port)
    try:
        if case == "413 oversized length":
            writer.write((f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{s.port}\r\nContent-Length: 5000\r\n\r\n")
                         .encode() + b"x" * 100)                             # the rest never comes
        else:
            size = 10 if case.startswith("408") else 1100
            writer.write(chunked_head(s.port) + b"800\r\n" + b"x" * size)    # the rest never comes
        await writer.drain()
        reply, closed_after = await reply_then_eof(reader)
        assert reply.startswith(b"HTTP/1.1 " + case[:3].encode()), reply[:40]
        assert b"\r\nConnection: close\r\n" in reply.split(b"\r\n\r\n")[0] + b"\r\n"
        assert closed_after < 1, closed_after
    finally:
        writer.close()
        await s.stop()


@pytest.mark.asyncio
async def test_ordinary_gets_keep_the_connection_alive(server):
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    try:
        for _ in range(2):
            writer.write(f"GET /api/v1/status HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n\r\n".encode())
            await writer.drain()
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            assert head.startswith(b"HTTP/1.1 200") and b"Connection: close" not in head
            length = int(next(line.split(b":")[1] for line in head.split(b"\r\n")
                              if line.lower().startswith(b"content-length")))
            await reader.readexactly(length)
    finally:
        writer.close()
