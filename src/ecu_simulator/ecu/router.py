"""Address routing: which ECU a request is for, and which protocols that ECU serves there.

The router is a table built from configuration. A :class:`Route` binds one address to one
ECU and to the protocols eligible on that address; a protocol that is not eligible never
sees the request, so no response is produced that would have to be discarded afterwards.

A *physical* address belongs to exactly one ECU. A *functional* address may be shared by
several ECUs; resolving it yields every eligible route in registration order, so multi-ECU
fan-out can be added without changing this interface. The router never inspects payloads
and never sees sockets.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from types import MappingProxyType

from ecu_simulator.transport.messages import DiagnosticRequest


class RouteConflictError(ValueError):
    """Two routes claim the same address in an incompatible way."""


@dataclass(frozen=True, slots=True)
class Route:
    """One address binding: the ECU, the protocols eligible there, and the policy for
    a service identifier none of those protocols claims.

    ``answer_unsupported`` is a property of this route. It is not derived from the
    addressing kind at request time: a functional route may answer unsupported services
    and a physical route may stay silent, if that is how it is configured.
    """

    ecu: str
    protocols: frozenset[str] = field(default_factory=frozenset)
    answer_unsupported: bool = True

    def permits(self, protocol_name: str) -> bool:
        return protocol_name in self.protocols


class AddressRouter:
    def __init__(self) -> None:
        self._physical: dict[int, Route] = {}
        self._functional: dict[int, tuple[Route, ...]] = {}

    # -- building ----------------------------------------------------------------------------

    def add_physical(
        self,
        address: int,
        ecu: str,
        protocols: Iterable[str],
        *,
        answer_unsupported: bool = True,
    ) -> None:
        """Route physically addressed requests on ``address`` to ``ecu`` (one ECU per address).

        A physically addressed request names one ECU, so by default a service identifier
        none of ``protocols`` claims is answered with NRC 0x11 (DEV-06).
        """
        route = _make_route(address, ecu, protocols, answer_unsupported)
        if address in self._functional:
            raise RouteConflictError(f"0x{address:X} is a functional address; it cannot be physical for {ecu!r}")
        current = self._physical.get(address)
        if current is not None and current != route:
            if current.ecu != ecu:
                raise RouteConflictError(
                    f"physical address 0x{address:X} is claimed by both {current.ecu!r} and {ecu!r}"
                )
            raise RouteConflictError(f"physical address 0x{address:X} already has a different route for {ecu!r}")
        self._physical[address] = route

    def add_functional(
        self,
        address: int,
        ecu: str,
        protocols: Iterable[str],
        *,
        answer_unsupported: bool = False,
    ) -> None:
        """Make ``ecu`` eligible for functionally addressed requests on ``address``.

        A functionally addressed request is a broadcast that several ECUs may ignore, so
        by default an unclaimed service identifier draws no response. Override it per
        route where that is wanted.
        """
        route = _make_route(address, ecu, protocols, answer_unsupported)
        if address in self._physical:
            owner = self._physical[address].ecu
            raise RouteConflictError(f"0x{address:X} is the physical address of {owner!r}; it cannot be functional")
        current = self._functional.get(address, ())
        for existing in current:
            if existing.ecu == ecu:
                if existing != route:
                    raise RouteConflictError(
                        f"functional address 0x{address:X} already has a different route for {ecu!r}"
                    )
                return
        self._functional[address] = current + (route,)

    # -- inspection --------------------------------------------------------------------------

    @property
    def physical_routes(self) -> MappingProxyType[int, Route]:
        return MappingProxyType(self._physical)

    @property
    def functional_routes(self) -> MappingProxyType[int, tuple[Route, ...]]:
        return MappingProxyType(self._functional)

    @property
    def ecu_names(self) -> frozenset[str]:
        names = {route.ecu for route in self._physical.values()}
        for routes in self._functional.values():
            names.update(route.ecu for route in routes)
        return frozenset(names)

    @property
    def routes(self) -> tuple[Route, ...]:
        """Every route, physical first, for configuration checks."""
        functional = tuple(route for routes in self._functional.values() for route in routes)
        return tuple(self._physical.values()) + functional

    # -- resolution --------------------------------------------------------------------------

    def resolve(self, request: DiagnosticRequest) -> tuple[Route, ...]:
        """Routes for ``request``: one for a physical address, zero or more for a functional
        one. An address with no route, or routed for the other addressing kind, yields ``()``.
        """
        if request.functional:
            return self._functional.get(request.target_address, ())
        route = self._physical.get(request.target_address)
        return (route,) if route is not None else ()


def _make_route(address: int, ecu: str, protocols: Iterable[str], answer_unsupported: bool) -> Route:
    if not isinstance(address, int) or address < 0:
        raise ValueError(f"address must be a non-negative integer, got {address!r}")
    if not isinstance(ecu, str) or not ecu:
        raise ValueError(f"ecu name must be a non-empty string, got {ecu!r}")
    names = frozenset(protocols)
    if not names:
        raise ValueError(f"route for 0x{address:X} on {ecu!r} names no protocols; it could never answer anything")
    for name in sorted(names):
        if not isinstance(name, str) or not name:
            raise ValueError(f"route for 0x{address:X} on {ecu!r} names an invalid protocol {name!r}")
    return Route(ecu, names, answer_unsupported=bool(answer_unsupported))
