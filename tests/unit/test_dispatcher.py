"""Dispatcher: the handler a transport calls. Router picks the ECU, the ECU answers."""

import dataclasses
import logging
import socket

import pytest

from ecu_simulator.ecu import AddressRouter, Dispatcher, Ecu, Route
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
        self.routes: list[Route] = []

    def handle(self, request, route):
        self.requests.append(request)
        self.routes.append(route)
        return super().handle(request, route)


ECHO = ("echo",)


def build(*ecus):
    router = AddressRouter()
    for ecu in ecus:
        ecu.register(Echo())
    return router, list(ecus)


def test_physical_request_reaches_the_routed_ecu():
    engine, tcm = Recording("engine"), Recording("tcm")
    router, ecus = build(engine, tcm)
    router.add_physical(0x7E0, "engine", ECHO)
    router.add_physical(0x7E1, "tcm", ECHO)
    dispatcher = Dispatcher(router, ecus)
    assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7E1)) == DiagnosticResponse(b"\x7e\x00")
    assert [r.target_address for r in tcm.requests] == [0x7E1] and engine.requests == []


def test_functional_request_reaches_the_single_eligible_ecu():
    engine = Recording("engine")
    router, ecus = build(engine)
    router.add_functional(0x7DF, "engine", ECHO)
    dispatcher = Dispatcher(router, ecus)
    assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7DF, functional=True)) == DiagnosticResponse(b"\x7e\x00")
    assert engine.requests[0].functional is True


def test_the_ecu_never_sees_the_transport_context():
    engine = Recording("engine")
    router, ecus = build(engine)
    router.add_physical(0x7E0, "engine", ECHO)
    Dispatcher(router, ecus)(DiagnosticRequest(b"\x3e\x00", 0x7E0, context=object()))
    assert engine.requests[0].context is None


def test_a_real_socket_in_the_transport_context_never_reaches_the_ecu():
    # Definition of Done for Phase 3: an Ecu never receives a socket. The transport puts
    # its endpoint (which owns the socket) in context; the dispatcher must strip it.
    left, right = socket.socketpair()
    try:
        engine = Recording("engine")
        router, ecus = build(engine)
        router.add_physical(0x7E0, "engine", ECHO)
        Dispatcher(router, ecus)(DiagnosticRequest(b"\x3e\x00", 0x7E0, context=left))
        (received,) = engine.requests
        held = [getattr(received, f.name) for f in dataclasses.fields(received)]
        assert not any(isinstance(v, socket.socket) for v in held), held
        assert not any(hasattr(v, "fileno") for v in held), held
    finally:
        left.close()
        right.close()


def test_unrouted_address_is_logged_and_dropped(caplog):
    router, ecus = build(Recording("engine"))
    router.add_physical(0x7E0, "engine", ECHO)
    dispatcher = Dispatcher(router, ecus)
    with caplog.at_level(logging.WARNING):
        assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7E5)) is None
    assert "0x7E5" in caplog.text and "no ECU" in caplog.text


def test_router_must_only_name_known_ecus():
    router, ecus = build(Ecu("engine"))
    router.add_physical(0x7E0, "engine", ECHO)
    router.add_physical(0x7E1, "tcm", ECHO)
    with pytest.raises(ValueError, match="tcm"):
        Dispatcher(router, ecus)


def test_duplicate_ecu_names_are_rejected():
    router, ecus = build(Ecu("engine"), Ecu("engine"))
    with pytest.raises(ValueError, match="engine"):
        Dispatcher(router, ecus)


def test_functional_fan_out_to_several_ecus_is_not_implemented_yet():
    router, ecus = build(Ecu("engine"), Ecu("tcm"))
    router.add_functional(0x7DF, "engine", ECHO)
    router.add_functional(0x7DF, "tcm", ECHO)
    with pytest.raises(NotImplementedError, match=r"0x7DF.*Phase 9"):
        Dispatcher(router, ecus)


def test_fan_out_added_after_construction_is_reported_clearly():
    # The router is a live object; a second eligible ECU added later must not surface
    # as an unpacking error inside the transport's readable callback.
    router, ecus = build(Recording("engine"), Recording("tcm"))
    router.add_functional(0x7DF, "engine", ECHO)
    dispatcher = Dispatcher(router, ecus)
    router.add_functional(0x7DF, "tcm", ECHO)
    with pytest.raises(NotImplementedError, match=r"0x7DF.*Phase 9"):
        dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7DF, functional=True))


def test_the_route_is_passed_to_the_ecu():
    engine = Recording("engine")
    router, ecus = build(engine)
    router.add_physical(0x7E0, "engine", ECHO)
    Dispatcher(router, ecus)(DiagnosticRequest(b"\x3e\x00", 0x7E0))
    (route,) = engine.routes
    assert route.ecu == "engine" and route.protocols == frozenset({"echo"})


def test_dispatcher_exposes_its_parts():
    engine = Ecu("engine")
    router, ecus = build(engine)
    router.add_physical(0x7E0, "engine", ECHO)
    dispatcher = Dispatcher(router, ecus)
    assert dispatcher.router is router and dispatcher.ecus == {"engine": engine}
