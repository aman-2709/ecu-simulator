import asyncio
import json

import pytest

from ecu_simulator.observe.handoff import HandOff
from ecu_simulator.observe.publisher import Publisher
from ecu_simulator.observe.wrapper import ObservedDispatcher
from ecu_simulator.transport import DiagnosticResponse
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress, IsoTpTransport
from tests.unit.fakes import FakeIsotpSocket, factory

PHYSICAL = EndpointConfig("obd_physical", IsoTpAddress(0x7E0, 0x7E8))
SECOND = EndpointConfig("obd_physical_b", IsoTpAddress(0x7E0, 0x7EF))   # DEV-25's shape


class Router:
    def resolve(self, request):
        class Route:
            ecu = "engine"
        return (Route(),)


@pytest.fixture(autouse=True)
def _clear():
    FakeIsotpSocket.instances.clear()
    yield
    for fake in FakeIsotpSocket.instances:
        if not fake.closed:
            fake.close()


async def settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def instrumented(log, busy=False):
    handoff = HandOff()
    def encode(rec, *_):
        log.append(f"publish {rec.seq}")
        return json.dumps({"seq": rec.seq})
    publisher = Publisher(handoff, Router(), {}, encode=encode)
    observed = ObservedDispatcher(lambda request: DiagnosticResponse(b"\x41\x0c\x0c\x80"), handoff, publisher.wake)
    return publisher, observed


def spy_sends(log):
    for fake in FakeIsotpSocket.instances:
        original = fake.send
        def send(data, _orig=original, _fake=fake):
            n = _orig(data)
            log.append(f"send {data.hex()}")
            return n
        fake.send = send


@pytest.mark.asyncio
async def test_O1_an_accepted_reply_is_handed_to_the_kernel_before_the_publisher_runs():
    log: list[str] = []
    publisher, observed = instrumented(log)
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(), check_environment=False)
    task = asyncio.create_task(publisher.run())
    await transport.start(observed)
    spy_sends(log)
    FakeIsotpSocket.instances[0].feed.send(b"\x01\x0c")
    await settle()
    await transport.stop()
    task.cancel()
    assert log == ["send 410c0c80", "publish 1"]


@pytest.mark.asyncio
async def test_O2_a_queued_reply_may_be_published_before_it_is_sent():
    log: list[str] = []
    publisher, observed = instrumented(log)
    transport = IsoTpTransport("vcan0", [PHYSICAL], socket_factory=factory(busy=True), check_environment=False)
    task = asyncio.create_task(publisher.run())
    await transport.start(observed)
    spy_sends(log)
    fake = FakeIsotpSocket.instances[0]
    fake.feed.send(b"\x01\x0c")
    await settle()
    assert log == ["publish 1"]                  # queued in endpoint.pending, not sent, yet published
    assert json.loads(publisher.history.snapshot()[0][1])["seq"] == 1
    fake.busy = False
    await settle()
    await transport.stop()
    task.cancel()
    assert log == ["publish 1", "send 410c0c80"]


@pytest.mark.asyncio
async def test_one_request_on_two_sockets_is_two_exchanges():
    # 0010 §4.4 last row, DEV-25: the kernel delivers one request to both sockets (measured, 0010 §12 E6).
    log: list[str] = []
    publisher, observed = instrumented(log)
    transport = IsoTpTransport("vcan0", [PHYSICAL, SECOND], socket_factory=factory(), check_environment=False)
    task = asyncio.create_task(publisher.run())
    await transport.start(observed)
    for fake in FakeIsotpSocket.instances:
        fake.feed.send(b"\x01\x0c")
    await settle()
    await transport.stop()
    task.cancel()
    assert publisher.published == 2 and observed.issued == 2
