"""Simulator runtime: build transport, router and ECUs from configuration, run, shut down.

One asyncio event loop owns every socket. Diagnostic handling stays synchronous:
``transport -> AddressRouter -> Ecu -> DiagnosticProtocol``. The transport calls the
:class:`Dispatcher`, the router maps the request's address to an ECU, the ECU dispatches
by service identifier to a registered protocol. No thread is started.

Until the YAML profile configuration lands (Phase 4), one implicit ECU named ``engine``
is derived from the legacy ``ecu_config.json``: it owns the OBD functional and physical
addresses and the UDS physical address, and registers the legacy OBD and UDS protocols.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ecu_simulator import ecu_config
from ecu_simulator.ecu import AddressRouter, Dispatcher, Ecu
from ecu_simulator.protocols.obd import LegacyObdProtocol
from ecu_simulator.protocols.uds import LegacyUdsProtocol
from ecu_simulator.transport import TransportError
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress, IsoTpOptions, IsoTpTransport

logger = logging.getLogger(__name__)

RESPONSE_ID_OFFSET = 0x8  # legacy rule: response id = request id + 8 (ISO 15765-4 style)

ENGINE_ECU = "engine"  # the single implicit ECU until profiles make the ECU list explicit

OBD_FUNCTIONAL = "obd_functional"
OBD_PHYSICAL = "obd_physical"
UDS_PHYSICAL = "uds_physical"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Everything the Phase 2 runtime needs, derived from the legacy ecu_config.json."""

    interface: str
    obd_functional_id: int
    obd_physical_id: int
    obd_response_id: int
    uds_request_id: int
    uds_response_id: int
    # ISO 15765-4 requires 8-byte frames on Classical CAN for OBD; the pad byte is a
    # convention (0x00 here, 0x55 and 0xAA are also common). UDS padding is left as it
    # was (off) until per-ECU configuration exists.
    obd_tx_padding: bool = True
    pad_byte: int = 0x00


def config_from_legacy(interface: str | None = None) -> RuntimeConfig:
    """Read addresses from the legacy JSON config; ``interface`` overrides its ``can_interface``."""
    obd_physical = ecu_config.get_obd_ecu_address()
    uds_request = ecu_config.get_uds_ecu_address()
    return RuntimeConfig(
        interface=interface or ecu_config.get_can_interface(),
        obd_functional_id=ecu_config.get_obd_broadcast_address(),
        obd_physical_id=obd_physical,
        obd_response_id=obd_physical + RESPONSE_ID_OFFSET,
        uds_request_id=uds_request,
        uds_response_id=uds_request + RESPONSE_ID_OFFSET,
    )


def build_endpoints(config: RuntimeConfig) -> list[EndpointConfig]:
    """ISO-TP sockets to open.

    OBD: a functional endpoint (rx 0x7DF) and a physical endpoint (rx 0x7E0), both
    answering on 0x7E8. Every OBD response is transmitted through the physical socket so
    that the tester's flow control on 0x7E0 reaches the transmitting state machine
    (ISO 15765-4); physically addressed requests are served as well (DEV-01 corrected).
    OBD frames are padded to DLC 8 (DEV-08 corrected). UDS: one physical endpoint,
    unpadded as before.
    """
    obd_options = IsoTpOptions(tx_padding=config.obd_tx_padding, pad_byte=config.pad_byte)
    uds_options = IsoTpOptions()
    return [
        EndpointConfig(
            OBD_FUNCTIONAL,
            IsoTpAddress(config.obd_functional_id, config.obd_response_id),
            functional=True,
            options=obd_options,
            reply_via=OBD_PHYSICAL,
        ),
        EndpointConfig(OBD_PHYSICAL, IsoTpAddress(config.obd_physical_id, config.obd_response_id), options=obd_options),
        EndpointConfig(UDS_PHYSICAL, IsoTpAddress(config.uds_request_id, config.uds_response_id), options=uds_options),
    ]


def build_ecus(config: RuntimeConfig) -> list[Ecu]:
    """The implicit ``engine`` ECU with the legacy OBD and UDS protocols registered."""
    engine = Ecu(ENGINE_ECU)
    engine.register(LegacyObdProtocol())
    engine.register(LegacyUdsProtocol())
    return [engine]


def build_router(config: RuntimeConfig) -> AddressRouter:
    """Every legacy address routes to the engine ECU; the OBD broadcast id is functional."""
    router = AddressRouter()
    router.add_functional(config.obd_functional_id, ENGINE_ECU)
    router.add_physical(config.obd_physical_id, ENGINE_ECU)
    router.add_physical(config.uds_request_id, ENGINE_ECU)
    return router


def build_dispatcher(config: RuntimeConfig) -> Dispatcher:
    return Dispatcher(build_router(config), build_ecus(config))


def check_routes(router: AddressRouter, endpoints: Iterable[EndpointConfig]) -> None:
    """Every receiving endpoint must have a route, and every route an endpoint.

    The endpoints and the routes are derived separately from the same configuration. If
    they disagree, a socket is open that nothing answers (requests are dropped with only
    a log line) or a route names an address no socket listens on. Both are startup
    errors, not runtime surprises.
    """
    listening = {e.address.rx_id: bool(e.functional) for e in endpoints if e.receive}
    routed: dict[int, bool] = {address: False for address in router.physical_routes}
    routed.update({address: True for address in router.functional_routes})
    for address, functional in sorted(listening.items()):
        if address not in routed:
            raise ValueError(f"no route for address 0x{address:X}: requests received there would be dropped")
        if routed[address] != functional:
            kind, other = ("functional", "physical") if functional else ("physical", "functional")
            raise ValueError(f"address 0x{address:X} is a {kind} endpoint but a {other} route")
    for address in sorted(routed):
        if address not in listening:
            raise ValueError(f"route for address 0x{address:X} has no endpoint receiving on it")


async def run(
    config: RuntimeConfig,
    *,
    stop: asyncio.Event | None = None,
    install_signal_handlers: bool = True,
    transport_factory: Callable[..., IsoTpTransport] = IsoTpTransport,
) -> None:
    """Start the transport, wait for ``stop`` (or SIGINT/SIGTERM), then shut down.

    Raises :class:`TransportError` if the transport cannot start; the caller reports it.
    """
    endpoints = build_endpoints(config)
    router = build_router(config)
    check_routes(router, endpoints)  # before any socket is opened
    dispatcher = Dispatcher(router, build_ecus(config))
    transport = transport_factory(config.interface, endpoints)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    started = False
    try:
        await transport.start(dispatcher)
        started = True
        if install_signal_handlers:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _request_stop, stop, sig)
                installed.append(sig)
        logger.info("ecu-simulator ready on %s", config.interface)
        await stop.wait()
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        await transport.stop()
        if started:
            logger.info("shutdown complete")


def _request_stop(stop: asyncio.Event, sig: signal.Signals) -> None:
    logger.info("received %s, shutting down", sig.name)
    stop.set()


def main(config: RuntimeConfig) -> int:
    """Run to completion; exit status 0 on clean shutdown, 2 on a transport/startup failure."""
    try:
        asyncio.run(run(config))
    except TransportError as error:
        logger.error("%s", error)
        return 2
    return 0
