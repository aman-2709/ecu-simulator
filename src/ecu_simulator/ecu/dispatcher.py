"""The request handler a transport calls: resolve the route, hand it to the ECU.

``transport -> route binding -> Ecu -> eligible protocol``. The dispatcher strips the
transport's opaque ``context`` and endpoint identity before the ECU sees the request, so
an ECU can never reach a socket through it. Shared functional addresses are resolved by
the endpoint on which the kernel delivered this copy of the request.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Iterable
from types import MappingProxyType

from ecu_simulator.ecu.ecu import Ecu
from ecu_simulator.ecu.router import AddressRouter
from ecu_simulator.transport.messages import DiagnosticRequest, DiagnosticResponse

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        router: AddressRouter,
        ecus: Iterable[Ecu],
        *,
        sync: Callable[[], None] | None = None,
    ) -> None:
        """``sync``, if given, is called once before each request is dispatched.

        It is a plain callable and this layer never learns what it does, so the dispatcher
        stays what it was: resolve the route, hand the request to the ECU. What it does in
        practice is bring domain state up to the time that has already passed, before the
        protocol reads that state -- which is where "lazy evaluation on read" is resolved,
        deliberately above VehicleState rather than inside it, so the protocols go on
        reading a number instead of asking a scenario for one.

        It is called once per request, before dispatch, and it advances nothing: two
        requests arriving at one instant leave identical state and get identical bytes.
        """
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
        self._router = router
        self._ecus = by_name
        self._sync = sync

    @property
    def router(self) -> AddressRouter:
        return self._router

    @property
    def ecus(self) -> MappingProxyType[str, Ecu]:
        return MappingProxyType(self._ecus)

    @property
    def sync(self) -> Callable[[], None] | None:
        return self._sync

    def __call__(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        if self._sync is not None:
            self._sync()
        routes = self._router.resolve(request)
        if not routes:
            logger.warning(
                "no ECU for %s request on 0x%X; dropped",
                "functional" if request.functional else "physical",
                request.target_address,
            )
            return None
        if len(routes) > 1:
            raise ValueError(
                f"functional address 0x{request.target_address:X} is shared by {[r.ecu for r in routes]}; "
                "the receiving endpoint identity is required"
            )
        route = routes[0]
        clean = dataclasses.replace(request, endpoint_name=None, context=None)
        return self._ecus[route.ecu].handle(clean, route)
