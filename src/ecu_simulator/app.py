"""Simulator runtime: build the transport from configuration, run it, shut it down cleanly.

One asyncio event loop owns every socket. Diagnostic handling stays synchronous: the
transport calls :class:`LegacyDispatcher`, which maps an endpoint name to the legacy
OBD or UDS service layer and wraps the bytes it returns. No thread is started.

This is the Phase 2 shape. The dispatcher is a placeholder for the Phase 3 router:
it routes by endpoint name, not by ECU, and knows nothing about addresses beyond what
the request record carries.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ecu_simulator import ecu_config
from ecu_simulator.obd import handler as obd_handler
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse, TransportError
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress, IsoTpOptions, IsoTpTransport
from ecu_simulator.uds import handler as uds_handler

logger = logging.getLogger(__name__)

RESPONSE_ID_OFFSET = 0x8  # legacy rule: response id = request id + 8 (ISO 15765-4 style)

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


Handler = Callable[[bytes], bytes | None]


class LegacyDispatcher:
    """Route requests to the legacy OBD or UDS service layer by endpoint name."""

    def __init__(self, handlers: dict[str, Handler] | None = None) -> None:
        self._handlers = handlers if handlers is not None else {}

    @classmethod
    def for_endpoints(cls, endpoints: Iterable[EndpointConfig]) -> LegacyDispatcher:
        handlers: dict[str, Handler] = {}
        for endpoint in endpoints:
            if endpoint.name.startswith("obd_"):
                handlers[endpoint.name] = obd_handler.handle
            elif endpoint.name.startswith("uds_"):
                handlers[endpoint.name] = uds_handler.handle
            else:
                raise ValueError(f"no legacy handler for endpoint {endpoint.name!r}")
        return cls(handlers)

    def __call__(self, request: DiagnosticRequest) -> DiagnosticResponse | None:
        endpoint = request.context
        name = getattr(endpoint, "name", None)
        handler = self._handlers.get(name) if name is not None else None
        if handler is None:
            logger.error("no handler for request on endpoint %r", name)
            return None
        logger.info("%s rx 0x%X request 0x%s", name, request.target_address, request.payload.hex())
        response = handler(request.payload)
        if response is None:
            logger.info("%s: no response", name)
            return None
        logger.info("%s response 0x%s", name, response.hex())
        return DiagnosticResponse(response)


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
    transport = transport_factory(config.interface, endpoints)
    dispatcher = LegacyDispatcher.for_endpoints(endpoints)
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
