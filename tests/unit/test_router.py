"""AddressRouter: target address -> ECU name(s). It never looks at payloads."""

import pytest

from ecu_simulator.ecu import AddressRouter, RouteConflictError
from ecu_simulator.transport import DiagnosticRequest


def physical(address, payload=b"\x3e\x00"):
    return DiagnosticRequest(payload, address)


def functional(address, payload=b"\x01\x00"):
    return DiagnosticRequest(payload, address, functional=True)


def test_physical_address_resolves_to_exactly_one_ecu():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    router.add_physical(0x7E1, "engine")
    router.add_physical(0x7E2, "tcm")
    assert router.resolve(physical(0x7E0)) == ("engine",)
    assert router.resolve(physical(0x7E1)) == ("engine",)
    assert router.resolve(physical(0x7E2)) == ("tcm",)


def test_functional_address_resolves_to_every_eligible_ecu_in_registration_order():
    router = AddressRouter()
    router.add_functional(0x7DF, "engine")
    router.add_functional(0x7DF, "tcm")
    assert router.resolve(functional(0x7DF)) == ("engine", "tcm")


def test_unknown_address_resolves_to_nothing():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    router.add_functional(0x7DF, "engine")
    assert router.resolve(physical(0x7E5)) == ()
    assert router.resolve(functional(0x7E5)) == ()


def test_addressing_kind_must_match_the_route():
    # A physical request on the functional id (or vice versa) is a transport misconfiguration,
    # not a route: it resolves to nothing instead of guessing.
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    router.add_functional(0x7DF, "engine")
    assert router.resolve(functional(0x7E0)) == ()
    assert router.resolve(physical(0x7DF)) == ()


def test_resolution_ignores_the_payload():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    assert router.resolve(physical(0x7E0, b"")) == ("engine",)
    assert router.resolve(physical(0x7E0, b"\xff" * 4095)) == ("engine",)


def test_same_physical_address_twice_for_the_same_ecu_is_idempotent():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    router.add_physical(0x7E0, "engine")
    assert router.resolve(physical(0x7E0)) == ("engine",)


def test_physical_address_claimed_by_two_ecus_is_a_conflict():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    with pytest.raises(RouteConflictError, match=r"0x7E0.*engine.*tcm"):
        router.add_physical(0x7E0, "tcm")


def test_one_address_cannot_be_both_physical_and_functional():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    with pytest.raises(RouteConflictError, match="0x7E0"):
        router.add_functional(0x7E0, "tcm")
    router.add_functional(0x7DF, "engine")
    with pytest.raises(RouteConflictError, match="0x7DF"):
        router.add_physical(0x7DF, "tcm")


def test_functional_route_lists_each_ecu_once():
    router = AddressRouter()
    router.add_functional(0x7DF, "engine")
    router.add_functional(0x7DF, "engine")
    assert router.resolve(functional(0x7DF)) == ("engine",)


def test_invalid_addresses_and_names_are_rejected():
    router = AddressRouter()
    with pytest.raises(ValueError):
        router.add_physical(-1, "engine")
    with pytest.raises(ValueError):
        router.add_functional(0x7DF, "")


def test_routes_are_inspectable_and_read_only():
    router = AddressRouter()
    router.add_physical(0x7E0, "engine")
    router.add_functional(0x7DF, "engine")
    assert dict(router.physical_routes) == {0x7E0: "engine"}
    assert dict(router.functional_routes) == {0x7DF: ("engine",)}
    assert router.ecu_names == frozenset({"engine"})
    with pytest.raises(TypeError):
        router.physical_routes[0x7E1] = "tcm"  # type: ignore[index]
