"""The request handler a transport calls: route by address, hand to the ECU, return its answer.

``transport -> AddressRouter -> Ecu -> DiagnosticProtocol``. The dispatcher strips the
transport's opaque ``context`` before the ECU sees the request, so an ECU can never
reach a socket through it. Functional fan-out to several ECUs is rejected at
construction until the transport can carry one response per ECU (Phase 9).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable
from types import MappingProxyType

from ecu_simulator.ecu.ecu import Ecu
from ecu_simulator.ecu.router import AddressRouter
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
        for address, names in router.functional_routes.items():
            if len(names) > 1:
                raise NotImplementedError(
                    f"functional address 0x{address:X} is shared by {list(names)}; "
                    "fan-out to several ECUs needs per-ECU response routing (Phase 9)"
                )
        self._router = router
        self._ecus = by_name

    @property
    def router(self) -> AddressRouter:
        return self._router

    @property
    def ecus(self) -> MappingProxyType[str, Ecu]:
        return MappingProxyType(self._ecus)

    def __call__(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        names = self._router.resolve(request)
        if not names:
            logger.warning(
                "no ECU for %s request on 0x%X; dropped",
                "functional" if request.functional else "physical",
                request.target_address,
            )
            return None
        (name,) = names
        return self._ecus[name].handle(dataclasses.replace(request, context=None))
