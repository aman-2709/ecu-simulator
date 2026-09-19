"""Protocol eligibility is a property of the route, not a filter applied to responses.

A route binds an address to one ECU and to the protocols that ECU serves on that
address. A protocol that is not eligible for a route never sees the request, so no
response is produced to discard. Nothing anywhere suppresses a response because it is
negative or because the request was functionally addressed.
"""

import pytest

from ecu_simulator.ecu import AddressRouter, Dispatcher, Ecu, Route
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse


class Spy:
    """Records every call, so "never invoked" is provable rather than inferred."""

    def __init__(self, name, sids, response=b"\x7e\x00"):
        self.name = name
        self.service_ids = frozenset(sids)
        self.response = response
        self.calls = []

    def handle(self, request):
        self.calls.append(request.payload)
        return self.response


OBD_SIDS = {0x01, 0x09}
UDS_SIDS = {0x10, 0x19}


def engine_with(obd, uds):
    ecu = Ecu("engine")
    ecu.register(obd)
    ecu.register(uds)
    return ecu


def build(obd, uds):
    """The shipped route layout: OBD alone on the functional id, both on the physical ids."""
    ecu = engine_with(obd, uds)
    router = AddressRouter()
    router.add_functional(0x7DF, "engine", protocols=("obd",), answer_unsupported=False)
    router.add_physical(0x7E0, "engine", protocols=("obd", "uds"))
    router.add_physical(0x7E1, "engine", protocols=("obd", "uds"))
    return Dispatcher(router, [ecu])


def functional(payload, address=0x7DF):
    return DiagnosticRequest(payload, address, functional=True)


def physical(payload, address):
    return DiagnosticRequest(payload, address)


# --- what each route permits -----------------------------------------------------------------


def test_obd_functional_route_permits_obd():
    obd, uds = Spy("obd", OBD_SIDS, b"\x41\x00\x01"), Spy("uds", UDS_SIDS)
    assert build(obd, uds)(functional(b"\x01\x00")) == DiagnosticResponse(b"\x41\x00\x01")
    assert obd.calls == [b"\x01\x00"]


def test_obd_functional_route_does_not_permit_uds():
    obd, uds = Spy("obd", OBD_SIDS), Spy("uds", UDS_SIDS, b"\x50\x01\x00\x1e\x0b\xb8")
    assert build(obd, uds)(functional(b"\x10\x01")) is None
    assert uds.calls == [], "UDS is not eligible on the functional route and must never be invoked"
    assert obd.calls == [], "the SID does not belong to OBD either"


def test_physical_obd_route_permits_obd():
    obd, uds = Spy("obd", OBD_SIDS, b"\x41\x00\x01"), Spy("uds", UDS_SIDS)
    assert build(obd, uds)(physical(b"\x01\x00", 0x7E0)) == DiagnosticResponse(b"\x41\x00\x01")
    assert obd.calls == [b"\x01\x00"]


def test_physical_uds_route_permits_uds():
    obd, uds = Spy("obd", OBD_SIDS), Spy("uds", UDS_SIDS, b"\x50\x01\x00\x1e\x0b\xb8")
    assert build(obd, uds)(physical(b"\x10\x01", 0x7E1)) == DiagnosticResponse(b"\x50\x01\x00\x1e\x0b\xb8")
    assert uds.calls == [b"\x10\x01"]


def test_an_ineligible_protocol_is_never_invoked_even_for_a_sid_it_claims():
    obd, uds = Spy("obd", OBD_SIDS), Spy("uds", UDS_SIDS)
    dispatcher = build(obd, uds)
    for payload in (b"\x10\x01", b"\x19\x02", b"\x10\x05"):
        dispatcher(functional(payload))
    assert uds.calls == []


# --- no response is filtered because it is negative -------------------------------------------


def test_a_negative_response_from_an_eligible_protocol_is_sent_on_a_functional_route():
    # The decisive test: eligibility, not the functional flag, decides what is answered.
    # An eligible protocol's negative response goes out on a functional route unchanged.
    obd = Spy("obd", OBD_SIDS, b"\x7f\x01\x12")
    dispatcher = build(obd, Spy("uds", UDS_SIDS))
    assert dispatcher(functional(b"\x01\x00")) == DiagnosticResponse(b"\x7f\x01\x12")
    assert obd.calls == [b"\x01\x00"]


def test_a_negative_response_from_an_eligible_protocol_is_sent_on_a_physical_route():
    uds = Spy("uds", UDS_SIDS, b"\x7f\x10\x12")
    dispatcher = build(Spy("obd", OBD_SIDS), uds)
    assert dispatcher(physical(b"\x10\x05", 0x7E1)) == DiagnosticResponse(b"\x7f\x10\x12")


def test_unsupported_service_policy_belongs_to_the_route_not_to_the_addressing_kind():
    # A functional route may answer unsupported services, and a physical route may stay
    # silent. Neither follows from request.functional.
    ecu = engine_with(Spy("obd", OBD_SIDS), Spy("uds", UDS_SIDS))
    router = AddressRouter()
    router.add_functional(0x7DF, "engine", protocols=("obd",), answer_unsupported=True)
    router.add_physical(0x7E0, "engine", protocols=("obd",), answer_unsupported=False)
    dispatcher = Dispatcher(router, [ecu])
    assert dispatcher(functional(b"\x22\xf1\x90")) == DiagnosticResponse(b"\x7f\x22\x11")
    assert dispatcher(physical(b"\x22\xf1\x90", 0x7E0)) is None


# --- configuration errors ---------------------------------------------------------------------


def test_a_route_naming_a_protocol_the_ecu_does_not_serve_is_rejected():
    ecu = engine_with(Spy("obd", OBD_SIDS), Spy("uds", UDS_SIDS))
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", protocols=("obd", "doip"))
    with pytest.raises(ValueError, match="doip"):
        Dispatcher(router, [ecu])


def test_a_route_must_name_at_least_one_protocol():
    router = AddressRouter()
    with pytest.raises(ValueError, match="no protocols"):
        router.add_physical(0x7E0, "engine", protocols=())


def test_routes_are_inspectable():
    router = AddressRouter()
    router.add_functional(0x7DF, "engine", protocols=("obd",), answer_unsupported=False)
    router.add_physical(0x7E0, "engine", protocols=("obd", "uds"))
    assert router.functional_routes[0x7DF] == (Route("engine", frozenset({"obd"}), answer_unsupported=False),)
    assert router.physical_routes[0x7E0] == Route("engine", frozenset({"obd", "uds"}), answer_unsupported=True)
