"""Simulator runtime: build transport, router and ECUs from a profile, run, shut down.

One asyncio event loop owns every socket. Diagnostic handling stays synchronous:
``transport -> AddressRouter -> Ecu -> DiagnosticProtocol``. The transport calls the
:class:`Dispatcher`, the router maps the request's address to a route, and the ECU
dispatches by service identifier to a protocol that route enables. No thread is started.

Everything comes from a validated YAML profile. The endpoints the transport opens and the
routes the router resolves are built from the same per-ECU endpoint list, and
:func:`check_routes` proves they agree before a socket is opened.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ecu_simulator.config import Profile
from ecu_simulator.config.legacy import legacy_data
from ecu_simulator.config.schema import EcuConfig, EndpointConfigModel
from ecu_simulator.dtc import DtcState, DtcStore
from ecu_simulator.ecu import AddressRouter, Dispatcher, Ecu
from ecu_simulator.protocols.obd import ObdProtocol
from ecu_simulator.protocols.uds import LegacyUdsProtocol
from ecu_simulator.transport import TransportError
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress, IsoTpOptions, IsoTpTransport
from ecu_simulator.vehicle import POWERTRAINS, CommonState, IceState, TractionBattery, VehicleState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """A validated profile plus the interface actually used."""

    profile: Profile
    interface: str

    @classmethod
    def build(cls, profile: Profile, interface: str | None = None) -> RuntimeConfig:
        """``interface`` from the command line overrides the profile's."""
        return cls(profile=profile, interface=interface or profile.transport.interface)


def endpoint_name(ecu_name: str, endpoint: EndpointConfigModel) -> str:
    """Endpoint names are unique per ECU in the profile and global on the transport."""
    return f"{ecu_name}.{endpoint.name}"


def build_endpoints(config: RuntimeConfig) -> list[EndpointConfig]:
    """Every ISO-TP socket in the profile, across every ECU."""
    endpoints: list[EndpointConfig] = []
    for ecu_name, ecu in config.profile.ecus.items():
        for endpoint in ecu.endpoints:
            endpoints.append(
                EndpointConfig(
                    endpoint_name(ecu_name, endpoint),
                    IsoTpAddress(endpoint.rx, endpoint.tx),
                    functional=endpoint.functional,
                    options=IsoTpOptions(tx_padding=endpoint.tx_padding, pad_byte=endpoint.pad_byte),
                    reply_via=(
                        endpoint_name(ecu_name, _endpoint_named(ecu, endpoint.reply_via))
                        if endpoint.reply_via is not None
                        else None
                    ),
                )
            )
    return endpoints


def build_router(config: RuntimeConfig) -> AddressRouter:
    """One route per endpoint, carrying the protocols that endpoint enables."""
    router = AddressRouter()
    for ecu_name, ecu in config.profile.ecus.items():
        for endpoint in ecu.endpoints:
            add = router.add_functional if endpoint.functional else router.add_physical
            add(
                endpoint.rx,
                ecu_name,
                tuple(endpoint.protocols),
                answer_unsupported=endpoint.answers_unsupported,
            )
    return router


def build_vehicle(config: RuntimeConfig) -> VehicleState:
    """The composed vehicle state the profile describes."""
    vehicle = config.profile.vehicle
    common = CommonState(
        vin=vehicle.vin,
        speed=vehicle.speed,
        ambient_temp=vehicle.ambient_temp,
        battery_voltage=vehicle.battery_voltage,
        obd_standard=vehicle.obd_standard,
    )
    powertrain_type = POWERTRAINS[vehicle.type]
    fields: dict[str, object] = {}
    if vehicle.engine is not None and powertrain_type is not POWERTRAINS["bev"]:
        engine = vehicle.engine
        fields["engine"] = IceState(
            rpm=engine.rpm,
            coolant_temp=engine.coolant_temp,
            intake_temp=engine.intake_temp,
            engine_load=engine.engine_load,
            throttle=engine.throttle,
            maf=engine.maf,
            map=engine.map,
            timing_advance=engine.timing_advance,
            short_fuel_trim=engine.short_fuel_trim,
            long_fuel_trim=engine.long_fuel_trim,
            runtime=engine.runtime,
            fuel_level=engine.fuel_level,
            fuel_type=engine.fuel_type,
        )
    if vehicle.battery is not None and powertrain_type is not POWERTRAINS["ice"]:
        fields["battery"] = TractionBattery(
            soc=vehicle.battery.soc, voltage=vehicle.battery.voltage, temp=vehicle.battery.temp
        )
    return VehicleState(common, powertrain_type(**fields))  # type: ignore[arg-type]


def build_dtc_store(ecu_config: EcuConfig) -> DtcStore:
    """The trouble codes this ECU starts with, in the order the profile lists them.

    Domain state only. The OBD and UDS views encode it; neither owns it, and both clear
    it through the one :meth:`~ecu_simulator.dtc.DtcStore.clear` (plan rule 7).
    """
    return DtcStore(
        DtcState(
            code=dtc.code,
            pending=dtc.pending,
            confirmed=dtc.confirmed,
            indicator_requested=dtc.indicator_requested,
        )
        for dtc in ecu_config.dtcs
    )


def build_ecus(config: RuntimeConfig, vehicle: VehicleState | None = None) -> list[Ecu]:
    """One Ecu per profile entry, with the protocols its endpoints reference registered.

    Every ECU observes the same vehicle, so two ECUs report the same speed.
    """
    vehicle = build_vehicle(config) if vehicle is None else vehicle
    ecus: list[Ecu] = []
    for ecu_name, ecu_config in config.profile.ecus.items():
        ecu = Ecu(ecu_name, dtc_store=build_dtc_store(ecu_config))
        for protocol_name in sorted({p for endpoint in ecu_config.endpoints for p in endpoint.protocols}):
            if protocol_name == ObdProtocol.name:
                ecu.register(ObdProtocol(vehicle, ecu_name=ecu_config.name, dtcs=[d.code for d in ecu_config.dtcs]))
            elif protocol_name == LegacyUdsProtocol.name:
                ecu.register(LegacyUdsProtocol())
            else:  # pragma: no cover - the schema rejects unknown protocol names
                raise ValueError(f"no protocol implementation named {protocol_name!r}")
        ecus.append(ecu)
    return ecus


def configure_legacy_modules(config: RuntimeConfig) -> None:
    """Point the frozen legacy UDS module at the profile's data.

    Temporary: it keeps its data in globals, so one process serves one ECU's trouble
    codes. Phase 6 replaces it. OBD no longer needs this: ObdProtocol reads the vehicle
    state and its ECU's configuration directly. See config/legacy.py.
    """
    from ecu_simulator.uds import services

    ecu_name, ecu = next(iter(config.profile.ecus.items()))
    services.configure(legacy_data(config.profile, ecu))
    logger.debug("legacy UDS module configured from ECU %r", ecu_name)


def build_dispatcher(config: RuntimeConfig) -> Dispatcher:
    configure_legacy_modules(config)
    return Dispatcher(build_router(config), build_ecus(config))


def check_routes(router: AddressRouter, endpoints: Iterable[EndpointConfig]) -> None:
    """Every receiving endpoint must have a route, and every route an endpoint.

    The endpoints and the routes are built from the same profile, so a disagreement means
    a bug here rather than a bad profile. Either way it would leave a socket open that
    nothing answers, or a route pointing at an address nothing listens on, so it is
    checked before any socket is opened.
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


def _endpoint_named(ecu: EcuConfig, name: str) -> EndpointConfigModel:
    for endpoint in ecu.endpoints:
        if endpoint.name == name:
            return endpoint
    raise ValueError(f"endpoint {name!r} is not defined on this ECU")  # unreachable: schema validated


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
    configure_legacy_modules(config)
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
