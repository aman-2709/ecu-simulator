import asyncio
import errno
import os

import pytest

from ecu_simulator.transport import AddressError, DiagnosticRequest, DiagnosticResponse, TransportError
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress, IsoTpOptions, IsoTpTransport
from tests.unit.fakes import FakeIsotpSocket, factory

FUNCTIONAL = EndpointConfig("obd_functional", IsoTpAddress(0x7DF, 0x7E8), functional=True)
PHYSICAL = EndpointConfig("obd_physical", IsoTpAddress(0x7E0, 0x7E8))
UDS = EndpointConfig("uds", IsoTpAddress(0x7E1, 0x7E9), options=IsoTpOptions(tx_padding=True))


@pytest.fixture(autouse=True)
def _clear_instances():
    FakeIsotpSocket.instances.clear()
    yield
    for fake in FakeIsotpSocket.instances:
        if not fake.closed:
            fake.close()


def fake_by_rx(rx_id: int) -> FakeIsotpSocket:
    for fake in FakeIsotpSocket.instances:
        for name, args in fake.calls:
            if name == "bind" and (args[1].get_rx_arbitration_id() & 0x1FFFFFFF) == rx_id:
                return fake
    raise AssertionError(f"no fake bound to rx 0x{rx_id:X}")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# --- construction ------------------------------------------------------------------------------


def test_duplicate_endpoint_names_and_address_pairs_are_rejected():
    with pytest.raises(AddressError, match="duplicate endpoint names"):
        IsoTpTransport("vcan0", [PHYSICAL, EndpointConfig("obd_physical", IsoTpAddress(0x7E1, 0x7E9))])
    with pytest.raises(AddressError, match="duplicate ISO-TP address pairs"):
        IsoTpTransport("vcan0", [PHYSICAL, EndpointConfig("other", IsoTpAddress(0x7E0, 0x7E8))])
    with pytest.raises(AddressError, match="at least one"):
        IsoTpTransport("vcan0", [])


def test_shared_functional_rx_with_distinct_tx_is_allowed():
    second = EndpointConfig("tcm_functional", IsoTpAddress(0x7DF, 0x7E9), functional=True)
    transport = IsoTpTransport("vcan0", [FUNCTIONAL, second])
    assert [e.name for e in transport.endpoints] == ["obd_functional", "tcm_functional"]


def test_reply_via_must_name_an_existing_endpoint():
    bad = EndpointConfig("obd_functional", IsoTpAddress(0x7DF, 0x7E8), functional=True, reply_via="nope")
    with pytest.raises(AddressError, match="unknown endpoint 'nope'"):
        IsoTpTransport("vcan0", [bad, PHYSICAL])


@pytest.mark.asyncio
async def test_reply_via_sends_the_response_on_the_designated_socket():
    functional = EndpointConfig("obd_functional", IsoTpAddress(0x7DF, 0x7E8), functional=True, reply_via="obd_physical")
    transport = IsoTpTransport("vcan0", [functional, PHYSICAL], socket_factory=factory(), check_environment=False)
    await transport.start(lambda request: DiagnosticResponse(b"\x41\x00\x08\x08\x00\x01"))
    fake_by_rx(0x7DF).feed.send(b"\x01\x00")
    await settle()
    await transport.stop()
    assert fake_by_rx(0x7DF).sent == []
    assert fake_by_rx(0x7E0).sent == [b"\x41\x00\x08\x08\x00\x01"]


@pytest.mark.asyncio
async def test_receive_false_opens_the_socket_but_never_delivers_requests():
    tx_only = EndpointConfig("obd_physical", IsoTpAddress(0x7E0, 0x7E8), receive=False)
    seen = []
    transport = IsoTpTransport("vcan0", [tx_only, UDS], socket_factory=factory(), check_environment=False)
    await transport.start(lambda request: seen.append(request))
    fake_by_rx(0x7E0).feed.send(b"\x01\x0d")
    fake_by_rx(0x7E1).feed.send(b"\x3e\x00")
    await settle()
    await transport.stop()
    assert [r.target_address for r in seen] == [0x7E1]
    assert all(f.closed for f in FakeIsotpSocket.instances)


# --- lifecycle ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_opens_all_sockets_and_stop_closes_them():
    transport = IsoTpTransport("vcan0", [FUNCTIONAL, PHYSICAL, UDS], socket_factory=factory(), check_environment=False)
    await transport.start(lambda request: None)
    assert transport.is_running and len(FakeIsotpSocket.instances) == 3
    assert all(f.timeout == 0.0 for f in FakeIsotpSocket.instances)
    await transport.stop()
    assert not transport.is_running and all(f.closed for f in FakeIsotpSocket.instances)
    await transport.stop()  # idempotent


@pytest.mark.asyncio
async def test_start_failure_closes_already_opened_sockets():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            return FakeIsotpSocket(bind_error=OSError(errno.ENODEV, "No such device"))
        return FakeIsotpSocket()

    transport = IsoTpTransport("vcan0", [FUNCTIONAL, PHYSICAL], socket_factory=flaky, check_environment=False)
    with pytest.raises(TransportError):
        await transport.start(lambda request: None)
    assert not transport.is_running
    assert all(f.closed for f in FakeIsotpSocket.instances)


@pytest.mark.asyncio
async def test_start_twice_is_an_error():
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(), check_environment=False)
    await transport.start(lambda request: None)
    with pytest.raises(TransportError, match="already started"):
        await transport.start(lambda request: None)
    await transport.stop()


@pytest.mark.asyncio
async def test_stop_leaves_no_open_file_descriptors():
    before = len(os.listdir("/proc/self/fd"))
    transport = IsoTpTransport("vcan0", [FUNCTIONAL, PHYSICAL, UDS], socket_factory=factory(), check_environment=False)
    await transport.start(lambda request: None)
    assert len(os.listdir("/proc/self/fd")) > before
    await transport.stop()
    assert len(os.listdir("/proc/self/fd")) == before


# --- dispatch ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_metadata_and_response_routing_per_endpoint():
    seen: list[DiagnosticRequest] = []

    def handler(request: DiagnosticRequest) -> DiagnosticResponse:
        seen.append(request)
        return DiagnosticResponse(payload=b"\x41" + request.payload[1:])

    transport = IsoTpTransport("vcan0", [FUNCTIONAL, PHYSICAL], socket_factory=factory(), check_environment=False)
    await transport.start(handler)
    fake_by_rx(0x7DF).feed.send(b"\x01\x00")
    fake_by_rx(0x7E0).feed.send(b"\x01\x0d")
    await settle()
    await transport.stop()

    by_target = {r.target_address: r for r in seen}
    assert by_target[0x7DF].functional is True and by_target[0x7DF].context is FUNCTIONAL
    assert by_target[0x7E0].functional is False and by_target[0x7E0].context is PHYSICAL
    assert fake_by_rx(0x7DF).sent == [b"\x41\x00"]
    assert fake_by_rx(0x7E0).sent == [b"\x41\x0d"]


@pytest.mark.asyncio
async def test_handler_none_sends_nothing_and_handler_exception_does_not_stop_the_loop():
    calls = []

    def handler(request: DiagnosticRequest):
        calls.append(request.payload)
        if request.payload == b"\xff":
            raise RuntimeError("boom")
        return None

    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(), check_environment=False)
    await transport.start(handler)
    fake = fake_by_rx(0x7E0)
    fake.feed.send(b"\xff")
    await settle()
    fake.feed.send(b"\x01\x0c")
    await settle()
    await transport.stop()
    assert calls == [b"\xff", b"\x01\x0c"] and fake.sent == []


@pytest.mark.asyncio
async def test_flow_control_timeout_on_recv_is_logged_and_service_continues(caplog):
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(), check_environment=False)
    await transport.start(lambda request: DiagnosticResponse(b"\x7e\x00"))
    fake = fake_by_rx(0x7E0)
    fake.recv_error = OSError(errno.ECOMM, "Communication error on send")
    fake.feed.send(b"x")  # make it readable; the fake raises ECOMM on this recv
    await settle()
    fake.feed.send(b"\x3e\x00")
    await settle()
    await transport.stop()
    assert "no flow control" in caplog.text
    assert fake.sent == [b"\x7e\x00"]


@pytest.mark.asyncio
async def test_busy_socket_queues_response_until_writable():
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(busy=True), check_environment=False)
    await transport.start(lambda request: DiagnosticResponse(b"\x49\x02" + b"V" * 18))
    fake = fake_by_rx(0x7E0)
    fake.feed.send(b"\x09\x02")
    await settle()
    assert fake.sent == []  # kernel still busy: queued
    fake.busy = False
    await asyncio.sleep(0.01)  # socketpair fd is writable, writer callback retries
    await transport.stop()
    assert fake.sent == [b"\x49\x02" + b"V" * 18]


@pytest.mark.asyncio
async def test_nonzero_delay_is_rejected_as_reserved():
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(), check_environment=False)
    await transport.start(lambda request: DiagnosticResponse(b"\x7e\x00", delay=0.5))
    fake = fake_by_rx(0x7E0)
    fake.feed.send(b"\x3e\x00")
    with pytest.raises(NotImplementedError):
        # the callback runs inside the loop; surface it by running the callback directly
        transport._on_readable(transport._endpoints[0])
    await transport.stop()
