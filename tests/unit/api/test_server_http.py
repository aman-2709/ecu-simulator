import asyncio
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
    assert conn.take_state() == snapshots.state_message(s.runtime)


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
    for path in ROUTES + ["/", "/api/v1/events"]:
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
async def test_the_placeholder_page_is_served(server, session):
    # M2 serves exactly this one file. Frontend files and rendering are M3's (decisions/0010 §7).
    async with session.get(url(server, "/")) as r:
        assert r.status == 200 and r.content_type == "text/html"
        assert "/api/v1/status" in await r.text()


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
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(chunked_head(server.port, keep_alive=False))
    for _ in range(40):                                                  # 2560 bytes, one chunk per write
        writer.write(b"40\r\n" + b"x" * 64 + b"\r\n")
        await writer.drain()
        await asyncio.sleep(0.005)
    writer.write(b"0\r\n\r\n")
    await writer.drain()
    reply = await asyncio.wait_for(reader.read(65536), 2)
    writer.close()
    assert reply.startswith(b"HTTP/1.1 413"), reply[:40]


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
