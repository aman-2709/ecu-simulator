"""AddressRouter: target address -> route(s). It never looks at payloads."""

import pytest

from ecu_simulator.ecu import AddressRouter, Route, RouteConflictError
from ecu_simulator.transport import DiagnosticRequest

OBD = ("obd",)
BOTH = ("obd", "uds")


def physical(address, payload=b"\x3e\x00"):
    return DiagnosticRequest(payload, address)


def functional(address, payload=b"\x01\x00"):
    return DiagnosticRequest(payload, address, functional=True)


def names(routes):
    return tuple(route.ecu for route in routes)


def test_physical_address_resolves_to_exactly_one_ecu():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    router.add_physical(0x7E1, "engine", BOTH)
    router.add_physical(0x7E2, "tcm", OBD)
    assert names(router.resolve(physical(0x7E0))) == ("engine",)
    assert names(router.resolve(physical(0x7E1))) == ("engine",)
    assert names(router.resolve(physical(0x7E2))) == ("tcm",)


def test_functional_address_resolves_to_every_eligible_ecu_in_registration_order():
    router = AddressRouter()
    router.add_functional(0x7DF, "engine", OBD)
    router.add_functional(0x7DF, "tcm", OBD)
    assert names(router.resolve(functional(0x7DF))) == ("engine", "tcm")


def test_a_route_carries_the_protocols_eligible_on_its_address():
    router = AddressRouter()
    router.add_functional(0x7DF, "engine", OBD)
    router.add_physical(0x7E1, "engine", BOTH)
    (broadcast,) = router.resolve(functional(0x7DF))
    (direct,) = router.resolve(physical(0x7E1))
    assert broadcast.protocols == frozenset({"obd"}) and broadcast.permits("obd")
    assert not broadcast.permits("uds")
    assert direct.protocols == frozenset({"obd", "uds"}) and direct.permits("uds")


def test_unsupported_service_policy_is_per_route_and_defaults_by_addressing_kind():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", OBD)
    router.add_functional(0x7DF, "engine", OBD)
    assert router.physical_routes[0x7E0].answer_unsupported is True
    assert router.functional_routes[0x7DF][0].answer_unsupported is False
    # Both defaults are overridable: the policy belongs to the route, not to the flag.
    other = AddressRouter()
    other.add_physical(0x7E0, "engine", OBD, answer_unsupported=False)
    other.add_functional(0x7DF, "engine", OBD, answer_unsupported=True)
    assert other.physical_routes[0x7E0].answer_unsupported is False
    assert other.functional_routes[0x7DF][0].answer_unsupported is True


def test_unknown_address_resolves_to_nothing():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    router.add_functional(0x7DF, "engine", OBD)
    assert router.resolve(physical(0x7E5)) == ()
    assert router.resolve(functional(0x7E5)) == ()


def test_addressing_kind_must_match_the_route():
    # A physical request on the functional id (or vice versa) is a transport
    # misconfiguration, not a route: it resolves to nothing instead of guessing.
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    router.add_functional(0x7DF, "engine", OBD)
    assert router.resolve(functional(0x7E0)) == ()
    assert router.resolve(physical(0x7DF)) == ()


def test_resolution_ignores_the_payload():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    assert names(router.resolve(physical(0x7E0, b""))) == ("engine",)
    assert names(router.resolve(physical(0x7E0, b"\xff" * 4095))) == ("engine",)


class PayloadTrap:
    """A request whose payload cannot be read: proves the router never looks at it."""

    def __init__(self, target_address, functional=False):
        self.target_address = target_address
        self.functional = functional

    @property
    def payload(self):
        raise AssertionError("the router inspected the payload")


def test_router_never_touches_the_payload_at_all():
    # Definition of Done for Phase 3: the router routes on addressing metadata only.
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    router.add_functional(0x7DF, "engine", OBD)
    assert names(router.resolve(PayloadTrap(0x7E0))) == ("engine",)
    assert names(router.resolve(PayloadTrap(0x7DF, functional=True))) == ("engine",)
    assert router.resolve(PayloadTrap(0x7E5)) == ()


def test_the_same_route_twice_is_idempotent():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    router.add_physical(0x7E0, "engine", BOTH)
    router.add_functional(0x7DF, "engine", OBD)
    router.add_functional(0x7DF, "engine", OBD)
    assert names(router.resolve(physical(0x7E0))) == ("engine",)
    assert names(router.resolve(functional(0x7DF))) == ("engine",)


def test_the_same_address_with_different_eligibility_is_a_conflict():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", OBD)
    with pytest.raises(RouteConflictError, match="0x7E0"):
        router.add_physical(0x7E0, "engine", BOTH)
    router.add_functional(0x7DF, "engine", OBD)
    with pytest.raises(RouteConflictError, match="0x7DF"):
        router.add_functional(0x7DF, "engine", OBD, answer_unsupported=True)


def test_physical_address_claimed_by_two_ecus_is_a_conflict():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    with pytest.raises(RouteConflictError, match=r"0x7E0.*engine.*tcm"):
        router.add_physical(0x7E0, "tcm", BOTH)


def test_one_address_cannot_be_both_physical_and_functional():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    with pytest.raises(RouteConflictError, match="0x7E0"):
        router.add_functional(0x7E0, "tcm", OBD)
    router.add_functional(0x7DF, "engine", OBD)
    with pytest.raises(RouteConflictError, match="0x7DF"):
        router.add_physical(0x7DF, "tcm", OBD)


def test_invalid_addresses_names_and_protocols_are_rejected():
    router = AddressRouter()
    with pytest.raises(ValueError):
        router.add_physical(-1, "engine", OBD)
    with pytest.raises(ValueError):
        router.add_functional(0x7DF, "", OBD)
    with pytest.raises(ValueError, match="no protocols"):
        router.add_functional(0x7DF, "engine", ())
    with pytest.raises(ValueError):
        router.add_physical(0x7E0, "engine", ("",))


def test_routes_are_inspectable_and_read_only():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine", BOTH)
    router.add_functional(0x7DF, "engine", OBD)
    assert dict(router.physical_routes) == {0x7E0: Route("engine", frozenset(BOTH), answer_unsupported=True)}
    assert dict(router.functional_routes) == {0x7DF: (Route("engine", frozenset(OBD), answer_unsupported=False),)}
    assert router.ecu_names == frozenset({"engine"})
    assert len(router.routes) == 2
    with pytest.raises(TypeError):
        router.physical_routes[0x7E1] = Route("tcm", frozenset(OBD))  # type: ignore[index]
