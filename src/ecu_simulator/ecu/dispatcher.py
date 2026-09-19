"""The request handler a transport calls: resolve the route, hand it to the ECU.

``transport -> route binding -> Ecu -> eligible protocol``. The dispatcher strips the
transport's opaque ``context`` before the ECU sees the request, so an ECU can never
reach a socket through it. Functional fan-out to several ECUs is rejected until the
transport can carry one response per ECU (Phase 9).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable
from types import MappingProxyType

from ecu_simulator.ecu.ecu import Ecu
from ecu_simulator.ecu.router import AddressRouter, Route
from ecu_simulator.transport.messages import DiagnosticRequest, DiagnosticResponse

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, router: AddressRouter, ecus: Iterable[Ecu]) -> None:
        by_name: dict[str, Ecu] = {}
        for ecu in ecus:
            if ecu.name in by_name:
                raise ValueError(f"duplicate ECU name {ecu.name!r}")
            by_name[ecu.name] = ecu
        unknown = sorted(router.ecu_names - set(by_name))
        if unknown:
            raise ValueError(f"router names ECUs that do not exist: {unknown}")
        for route in router.routes:
            served = {protocol.name for protocol in by_name[route.ecu].protocols}
            missing = sorted(route.protocols - served)
            if missing:
                raise ValueError(
                    f"route for ECU {route.ecu!r} enables protocols it does not serve: {missing} "
                    f"(registered: {sorted(served)})"
                )
        for address, routes in router.functional_routes.items():
            _reject_fan_out(address, routes)
        self._router = router
        self._ecus = by_name

    @property
    def router(self) -> AddressRouter:
        return self._router

    @property
    def ecus(self) -> MappingProxyType[str, Ecu]:
        return MappingProxyType(self._ecus)

    def __call__(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        routes = self._router.resolve(request)
        if not routes:
            logger.warning(
                "no ECU for %s request on 0x%X; dropped",
                "functional" if request.functional else "physical",
                request.target_address,
            )
            return None
        # The router is a live object and may have gained routes since construction.
        _reject_fan_out(request.target_address, routes)
        route = routes[0]
        return self._ecus[route.ecu].handle(dataclasses.replace(request, context=None), route)


def _reject_fan_out(address: int, routes: tuple[Route, ...]) -> None:
    if len(routes) > 1:
        raise NotImplementedError(
            f"functional address 0x{address:X} is shared by {[r.ecu for r in routes]}; "
            "fan-out to several ECUs needs per-ECU response routing (Phase 9)"
        )
