"""Dispatcher: the handler a transport calls. Router picks the ECU, the ECU answers."""

import logging

import pytest

from ecu_simulator.ecu import AddressRouter, Dispatcher, Ecu
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


class Echo:
    name = "echo"
    service_ids = frozenset({0x3E})

    def handle(self, request):
        return b"\x7e" + request.payload[1:]


class Recording(Ecu):
    def __init__(self, name):
        super().__init__(name)
        self.requests: list[DiagnosticRequest] = []

    def handle(self, request):
        self.requests.append(request)
        return super().handle(request)


def build(*ecus):
    router = AddressRouter()
    for ecu in ecus:
        ecu.register(Echo())
    return router, list(ecus)


def test_physical_request_reaches_the_routed_ecu():
    engine, tcm = Recording("engine"), Recording("tcm")
    router, ecus = build(engine, tcm)
    router.add_physical(0x7E0, "engine")
    router.add_physical(0x7E1, "tcm")
    dispatcher = Dispatcher(router, ecus)
    assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7E1)) == DiagnosticResponse(b"\x7e\x00")
    assert [r.target_address for r in tcm.requests] == [0x7E1] and engine.requests == []


def test_functional_request_reaches_the_single_eligible_ecu():
    engine = Recording("engine")
    router, ecus = build(engine)
    router.add_functional(0x7DF, "engine")
    dispatcher = Dispatcher(router, ecus)
    assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7DF, functional=True)) == DiagnosticResponse(b"\x7e\x00")
    assert engine.requests[0].functional is True


def test_the_ecu_never_sees_the_transport_context():
    engine = Recording("engine")
    router, ecus = build(engine)
    router.add_physical(0x7E0, "engine")
    Dispatcher(router, ecus)(DiagnosticRequest(b"\x3e\x00", 0x7E0, context=object()))
    assert engine.requests[0].context is None


def test_unrouted_address_is_logged_and_dropped(caplog):
    router, ecus = build(Recording("engine"))
    router.add_physical(0x7E0, "engine")
    dispatcher = Dispatcher(router, ecus)
    with caplog.at_level(logging.WARNING):
        assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7E5)) is None
    assert "0x7E5" in caplog.text and "no ECU" in caplog.text


def test_router_must_only_name_known_ecus():
    router, ecus = build(Ecu("engine"))
    router.add_physical(0x7E0, "engine")
    router.add_physical(0x7E1, "tcm")
    with pytest.raises(ValueError, match="tcm"):
        Dispatcher(router, ecus)


def test_duplicate_ecu_names_are_rejected():
    router, ecus = build(Ecu("engine"), Ecu("engine"))
    with pytest.raises(ValueError, match="engine"):
        Dispatcher(router, ecus)


def test_functional_fan_out_to_several_ecus_is_not_implemented_yet():
    router, ecus = build(Ecu("engine"), Ecu("tcm"))
    router.add_functional(0x7DF, "engine")
    router.add_functional(0x7DF, "tcm")
    with pytest.raises(NotImplementedError, match=r"0x7DF.*Phase 9"):
        Dispatcher(router, ecus)


def test_dispatcher_exposes_its_parts():
    engine = Ecu("engine")
    router, ecus = build(engine)
    router.add_physical(0x7E0, "engine")
    dispatcher = Dispatcher(router, ecus)
    assert dispatcher.router is router and dispatcher.ecus == {"engine": engine}
