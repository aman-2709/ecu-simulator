import asyncio

import pytest

aiohttp = pytest.importorskip("aiohttp", reason="needs the optional [gui] extra (aiohttp)")

import socket  # noqa: E402

from ecu_simulator import app  # noqa: E402
from ecu_simulator.api.options import ApiOptions, ApiStartupError  # noqa: E402
from ecu_simulator.observe.wrapper import ObservedDispatcher  # noqa: E402
from tests.unit.test_api_wiring import Capture, shipped  # noqa: E402


@pytest.mark.asyncio
async def test_api_on_hands_the_transport_the_observed_dispatcher():
    Capture.handlers.clear()
    stop = asyncio.Event()
    stop.set()
    await app.run(shipped(), stop=stop, install_signal_handlers=False, transport_factory=Capture,
                  api=ApiOptions("127.0.0.1", 0, "p", "t"))
    (handler,) = Capture.handlers
    assert isinstance(handler, ObservedDispatcher)


@pytest.mark.asyncio
async def test_a_busy_api_port_fails_before_any_can_socket_opens():  # Review Focus 4
    Capture.instances.clear()
    Capture.handlers.clear()
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        with pytest.raises(ApiStartupError):
            await app.run(shipped(), install_signal_handlers=False, transport_factory=Capture,
                          api=ApiOptions("127.0.0.1", holder.getsockname()[1], "p", "t"))
    finally:
        holder.close()
    # The transport may be constructed (its constructor opens no socket), but never started.
    assert len(Capture.instances) <= 1
    assert Capture.handlers == [], "the transport must not have been started"


@pytest.mark.asyncio
async def test_a_transport_constructor_that_raises_leaves_no_api_listening():
    from ecu_simulator.transport.errors import AddressError

    def refuse(interface, endpoints):
        raise AddressError("duplicate ISO-TP address pairs")
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    with pytest.raises(AddressError):
        await app.run(shipped(), install_signal_handlers=False, transport_factory=refuse,
                      api=ApiOptions("127.0.0.1", port, "p", "t"))
    with pytest.raises(ConnectionRefusedError):                          # nothing is listening on the port
        await asyncio.open_connection("127.0.0.1", port)
