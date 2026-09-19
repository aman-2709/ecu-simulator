"""Ecu: explicit SID registration, conflict detection, socket-free request handling.

Every ``handle`` call carries the route the request arrived on, which names the protocols
this ECU serves there. Dispatch is restricted to those; nothing filters a response after
a protocol has produced it.
"""

import logging

import pytest

from ecu_simulator.ecu import Ecu, Route, ServiceConflictError
from ecu_simulator.protocols.base import ServiceRequest
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


class FakeProtocol:
    """Answers every registered SID by echoing the request with the positive-response bit set."""

    def __init__(self, name, sids, *, silent=False):
        self.name = name
        self.service_ids = frozenset(sids)
        self.silent = silent
        self.seen: list[ServiceRequest] = []

    def handle(self, request):
        self.seen.append(request)
        if self.silent:
            return None
        return bytes([request.sid + 0x40]) + request.payload[1:]


def route(*protocols, answer_unsupported=True, ecu="engine"):
    return Route(ecu, frozenset(protocols), answer_unsupported=answer_unsupported)


def physical(payload, address=0x7E1):
    return DiagnosticRequest(payload, address)


def functional(payload, address=0x7DF):
    return DiagnosticRequest(payload, address, functional=True)


# --- registration ---------------------------------------------------------------------------


def test_registered_protocol_serves_its_sids():
    ecu = Ecu("engine")
    obd = FakeProtocol("obd", {0x01, 0x03, 0x09})
    ecu.register(obd)
    assert ecu.handle(physical(b"\x01\x0d"), route("obd")) == DiagnosticResponse(b"\x41\x0d")
    assert ecu.protocol_for(0x03) is obd
    assert ecu.protocol_for(0x10) is None
    assert dict(ecu.service_ids) == {0x01: "obd", 0x03: "obd", 0x09: "obd"}


def test_two_protocols_claiming_one_sid_is_a_conflict_and_leaves_the_ecu_unchanged():
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01, 0x03}))
    with pytest.raises(ServiceConflictError, match=r"engine.*0x03.*obd.*other"):
        ecu.register(FakeProtocol("other", {0x03, 0x22}))
    assert ecu.protocol_for(0x22) is None, "a rejected registration must not register partially"
    assert [p.name for p in ecu.protocols] == ["obd"]


def test_registration_rejects_duplicate_names_empty_and_out_of_range_sids():
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}))
    with pytest.raises(ServiceConflictError, match="obd"):
        ecu.register(FakeProtocol("obd", {0x10}))
    with pytest.raises(ValueError, match="no service"):
        ecu.register(FakeProtocol("empty", set()))
    with pytest.raises(ValueError, match="0x100"):
        ecu.register(FakeProtocol("wide", {0x100}))


def test_a_non_integer_sid_is_reported_as_such():
    # The message must name the offending value, not fail while formatting it.
    with pytest.raises(ValueError, match="'0x10'"):
        Ecu("engine").register(FakeProtocol("stringly", {"0x10"}))


def test_ecu_name_must_be_non_empty():
    with pytest.raises(ValueError):
        Ecu("")


# --- handling -------------------------------------------------------------------------------


def test_request_is_dispatched_by_sid_within_the_routes_protocols():
    ecu = Ecu("engine")
    obd, uds = FakeProtocol("obd", {0x01}), FakeProtocol("uds", {0x10})
    ecu.register(obd)
    ecu.register(uds)
    both = route("obd", "uds")
    assert ecu.handle(physical(b"\x10\x03", address=0x7E0), both) == DiagnosticResponse(b"\x50\x03")
    functional_obd = route("obd", answer_unsupported=False)
    assert ecu.handle(functional(b"\x01\x00"), functional_obd) == DiagnosticResponse(b"\x41\x00")
    assert [r.payload for r in uds.seen] == [b"\x10\x03"]
    assert obd.seen[0].functional is True and uds.seen[0].functional is False


def test_a_protocol_the_route_does_not_enable_is_never_invoked():
    ecu = Ecu("engine")
    obd, uds = FakeProtocol("obd", {0x01}), FakeProtocol("uds", {0x10})
    ecu.register(obd)
    ecu.register(uds)
    assert ecu.handle(functional(b"\x10\x03"), route("obd", answer_unsupported=False)) is None
    assert uds.seen == []


def test_a_sid_the_route_does_not_enable_follows_the_routes_unsupported_policy():
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}))
    ecu.register(FakeProtocol("uds", {0x10}))
    assert ecu.handle(physical(b"\x10\x03"), route("obd")) == DiagnosticResponse(b"\x7f\x10\x11")
    assert ecu.handle(physical(b"\x10\x03"), route("obd", answer_unsupported=False)) is None


def test_protocol_returning_none_means_no_response():
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}, silent=True))
    assert ecu.handle(functional(b"\x01\x0c"), route("obd")) is None


def test_empty_payload_gets_no_response(caplog):
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}))
    with caplog.at_level(logging.WARNING):
        assert ecu.handle(physical(b""), route("obd")) is None
    assert "empty" in caplog.text


def test_a_route_for_another_ecu_is_rejected():
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}))
    with pytest.raises(ValueError, match="tcm"):
        ecu.handle(physical(b"\x01\x00"), route("obd", ecu="tcm"))


# --- responses are never filtered after the fact ----------------------------------------------


class NegativeProtocol:
    """A protocol that answers with a negative response, as the legacy UDS layer does."""

    name = "uds"
    service_ids = frozenset({0x10})

    def handle(self, request):
        return b"\x7f\x10\x12"


@pytest.mark.parametrize("kind", ["functional", "physical"])
def test_an_eligible_protocols_negative_response_is_sent_whatever_the_addressing(kind):
    # There is no rule anywhere that discards a response for being negative or for
    # arriving functionally addressed. Eligibility alone decides what is answered.
    request = functional(b"\x10\x05") if kind == "functional" else physical(b"\x10\x05")
    ecu = Ecu("engine")
    ecu.register(NegativeProtocol())
    assert ecu.handle(request, route("uds")) == DiagnosticResponse(b"\x7f\x10\x12")


def test_positive_response_is_sent_for_functionally_addressed_requests():
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}))
    assert ecu.handle(functional(b"\x01\x00"), route("obd")) == DiagnosticResponse(b"\x41\x00")


# --- unregistered service identifiers ---------------------------------------------------------


def test_unregistered_sid_follows_the_routes_policy_not_the_addressing_kind():
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}))
    assert ecu.handle(functional(b"\x22\xf1\x90"), route("obd", answer_unsupported=False)) is None
    # A functional route configured to answer does answer; a physical one configured to
    # stay silent does not.
    assert ecu.handle(functional(b"\x22\xf1\x90"), route("obd")) == DiagnosticResponse(b"\x7f\x22\x11")
    assert ecu.handle(physical(b"\x22\xf1\x90"), route("obd", answer_unsupported=False)) is None


@pytest.mark.parametrize("payload, expected", [(b"\x22\xf1\x90", b"\x7f\x22\x11"), (b"\x27\x01", b"\x7f\x27\x11")])
def test_unregistered_sid_on_a_physical_route_gets_nrc_0x11(payload, expected):
    # DEV-06 corrected: serviceNotSupported, from the route's policy.
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("obd", {0x01}))
    assert ecu.handle(physical(payload), route("obd")) == DiagnosticResponse(expected)


def test_ecu_logs_request_and_response(caplog):
    ecu = Ecu("engine")
    ecu.register(FakeProtocol("uds", {0x10}))
    with caplog.at_level(logging.INFO):
        ecu.handle(physical(b"\x10\x01"), route("uds"))
    assert "0x7E1" in caplog.text and "1001" in caplog.text and "5001" in caplog.text
