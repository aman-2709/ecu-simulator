"""Address routing: which ECU(s) a request is for, decided from addressing metadata only.

The router is a table built from configuration. A *physical* address belongs to exactly
one ECU. A *functional* address may be shared by several ECUs; resolving it yields every
eligible ECU in registration order, so multi-ECU fan-out can be added without changing
this interface. The router never inspects payloads and never sees sockets.
"""

from __future__ import annotations

from types import MappingProxyType

from ecu_simulator.transport.messages import DiagnosticRequest


class RouteConflictError(ValueError):
    """Two routes claim the same address in an incompatible way."""


class AddressRouter:
    def __init__(self) -> None:
        self._physical: dict[int, str] = {}
        self._functional: dict[int, tuple[str, ...]] = {}

    # -- building ----------------------------------------------------------------------------

    def add_physical(self, address: int, ecu: str) -> None:
        """Route physically addressed requests on ``address`` to ``ecu`` (one ECU per address)."""
        _check(address, ecu)
        if address in self._functional:
            raise RouteConflictError(f"0x{address:X} is a functional address; it cannot be physical for {ecu!r}")
        owner = self._physical.get(address)
        if owner is not None and owner != ecu:
            raise RouteConflictError(f"physical address 0x{address:X} is claimed by both {owner!r} and {ecu!r}")
        self._physical[address] = ecu

    def add_functional(self, address: int, ecu: str) -> None:
        """Make ``ecu`` eligible for functionally addressed requests on ``address``."""
        _check(address, ecu)
        if address in self._physical:
            owner = self._physical[address]
            raise RouteConflictError(f"0x{address:X} is the physical address of {owner!r}; it cannot be functional")
        current = self._functional.get(address, ())
        if ecu not in current:
            self._functional[address] = current + (ecu,)

    # -- inspection --------------------------------------------------------------------------

    @property
    def physical_routes(self) -> MappingProxyType[int, str]:
        return MappingProxyType(self._physical)

    @property
    def functional_routes(self) -> MappingProxyType[int, tuple[str, ...]]:
        return MappingProxyType(self._functional)

    @property
    def ecu_names(self) -> frozenset[str]:
        names = set(self._physical.values())
        for eligible in self._functional.values():
            names.update(eligible)
        return frozenset(names)

    # -- resolution --------------------------------------------------------------------------

    def resolve(self, request: DiagnosticRequest) -> tuple[str, ...]:
        """ECU names for ``request``: one for a physical route, one or more for a functional one.

        An address with no route, or routed for the other addressing kind, yields ``()``.
        """
        if request.functional:
            return self._functional.get(request.target_address, ())
        owner = self._physical.get(request.target_address)
        return (owner,) if owner is not None else ()


def _check(address: int, ecu: str) -> None:
    if not isinstance(address, int) or address < 0:
        raise ValueError(f"address must be a non-negative integer, got {address!r}")
    if not isinstance(ecu, str) or not ecu:
        raise ValueError(f"ecu name must be a non-empty string, got {ecu!r}")
