"""Golden tests for the legacy socket listeners (obd/listener.py, uds/listener.py).

The listeners import the `isotp` package at module level and loop forever on
`recv()`. To characterize them without a CAN interface, a fake `isotp` module is
injected into sys.modules and its `recv()` raises `_Stop` once the scripted requests
are exhausted. This pins the socket wiring and the DEV-01 defect: the OBD listener binds
a socket on the physical request ID but never reads from it.
"""
import importlib
import sys
import types

import pytest

from tests.characterization.conftest import xfail_deviation


class _Stop(Exception):
    """Raised by the fake socket to break out of the listener's infinite loop."""


class FakeAddress:
    def __init__(self, rxid, txid):
        self.rxid = rxid
        self.txid = txid


class FakeIsotpSocket:
    instances = []
    scripts = {}  # rxid -> list of request payloads to deliver, then _Stop

    def __init__(self):
        self.interface = None
        self.rxid = None
        self.txid = None
        self.sent = []
        self.recv_calls = 0
        self._script = []
        FakeIsotpSocket.instances.append(self)

    def bind(self, interface, address):
        self.interface = interface
        self.rxid = address.rxid
        self.txid = address.txid
        self._script = list(FakeIsotpSocket.scripts.get(self.rxid, []))

    def recv(self):
        self.recv_calls += 1
        if self._script:
            return self._script.pop(0)
        raise _Stop

    def send(self, data):
        self.sent.append(bytes(data))

    @classmethod
    def by_rxid(cls, rxid):
        (sock,) = [s for s in cls.instances if s.rxid == rxid]
        return sock


@pytest.fixture
def fake_isotp(monkeypatch):
    module = types.ModuleType("isotp")
    module.socket = FakeIsotpSocket
    module.Address = FakeAddress
    monkeypatch.setitem(sys.modules, "isotp", module)
    FakeIsotpSocket.instances.clear()
    FakeIsotpSocket.scripts.clear()
    yield module


def _load(monkeypatch, name):
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    return module


@pytest.fixture
def obd_listener(fake_isotp, monkeypatch, reset_speed):
    yield _load(monkeypatch, "obd.listener")
    monkeypatch.delitem(sys.modules, "obd.listener", raising=False)


@pytest.fixture
def uds_listener(fake_isotp, monkeypatch):
    yield _load(monkeypatch, "uds.listener")
    monkeypatch.delitem(sys.modules, "uds.listener", raising=False)


# --- OBD listener --------------------------------------------------------------------------

def test_obd_listener_binds_functional_and_physical_sockets_with_same_response_id(obd_listener):
    with pytest.raises(_Stop):
        obd_listener.start()
    functional = FakeIsotpSocket.by_rxid(0x7DF)
    physical = FakeIsotpSocket.by_rxid(0x7E0)
    assert (functional.interface, functional.txid) == ("vcan0", 0x7E8)
    assert (physical.interface, physical.txid) == ("vcan0", 0x7E8)
    assert len(FakeIsotpSocket.instances) == 2


def test_obd_listener_answers_functional_request_via_physical_socket(obd_listener):
    FakeIsotpSocket.scripts[0x7DF] = [b"\x01\x0d", b"\x09\x02"]
    with pytest.raises(_Stop):
        obd_listener.start()
    functional = FakeIsotpSocket.by_rxid(0x7DF)
    physical = FakeIsotpSocket.by_rxid(0x7E0)
    assert functional.sent == []
    assert physical.sent == [b"\x41\x0d\x00", b"\x49\x02\x00TESTVIN0123456789"]


def test_obd_listener_never_reads_the_physical_socket_today(obd_listener):
    # DEV-01: a request on 0x7E0 is queued on a socket whose recv() is never called.
    FakeIsotpSocket.scripts[0x7E0] = [b"\x01\x0d"]
    with pytest.raises(_Stop):
        obd_listener.start()
    physical = FakeIsotpSocket.by_rxid(0x7E0)
    assert physical.recv_calls == 0
    assert physical.sent == []


@xfail_deviation("DEV-01", "physically addressed OBD requests are never consumed")
def test_obd_listener_answers_physical_request_corrected(obd_listener):
    FakeIsotpSocket.scripts[0x7E0] = [b"\x01\x0d"]
    with pytest.raises(_Stop):
        obd_listener.start()
    physical = FakeIsotpSocket.by_rxid(0x7E0)
    assert physical.sent == [b"\x41\x0d\x00"]


def test_obd_listener_sends_nothing_for_unsupported_pid_and_empty_payload(obd_listener):
    FakeIsotpSocket.scripts[0x7DF] = [b"\x01\x0c", b"", b"\x04"]
    with pytest.raises(_Stop):
        obd_listener.start()
    assert FakeIsotpSocket.by_rxid(0x7E0).sent == []


def test_obd_listener_multi_pid_request_answers_only_the_first_pid(obd_listener):
    # DEV-18: ISO 15765-4 allows several PIDs per Mode 01 request; only request[1] is used.
    FakeIsotpSocket.scripts[0x7DF] = [b"\x01\x0d\x2f\x51"]
    with pytest.raises(_Stop):
        obd_listener.start()
    assert FakeIsotpSocket.by_rxid(0x7E0).sent == [b"\x41\x0d\x00"]


@pytest.mark.parametrize(
    "payload, expected",
    [(None, (None, None)), (b"", (None, None)), (b"\x01", (None, 0x01)), (b"\x01\x0d", (0x0D, 0x01)), (b"\x01\x0d\x0c", (0x0D, 0x01))],
)
def test_obd_listener_sid_pid_parsing(obd_listener, payload, expected):
    assert obd_listener.get_sid_and_pid(payload) == expected


# --- UDS listener --------------------------------------------------------------------------

def test_uds_listener_binds_one_physical_socket(uds_listener):
    with pytest.raises(_Stop):
        uds_listener.start()
    (sock,) = FakeIsotpSocket.instances
    assert (sock.interface, sock.rxid, sock.txid) == ("vcan0", 0x7E1, 0x7E9)


def test_uds_listener_answers_on_the_same_socket_and_stays_silent_for_unsupported_sid(uds_listener):
    FakeIsotpSocket.scripts[0x7E1] = [b"\x10\x01", b"\x22\xf1\x90", b"", b"\x19\x02\xff"]
    with pytest.raises(_Stop):
        uds_listener.start()
    (sock,) = FakeIsotpSocket.instances
    assert sock.sent == [b"\x50\x01\x00\x1e\x0b\xb8", b"\x7f\x19\x13"]
