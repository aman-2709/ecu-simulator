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
import contextlib
import logging
import signal
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass

from ecu_simulator.clock import Clock, MonotonicClock
from ecu_simulator.config import Profile
from ecu_simulator.config.schema import EcuConfig, EndpointConfigModel
from ecu_simulator.dtc import DtcState, DtcStore
from ecu_simulator.ecu import AddressRouter, Dispatcher, Ecu
from ecu_simulator.protocols.obd import ObdProtocol
from ecu_simulator.protocols.uds import UdsProtocol
from ecu_simulator.protocols.uds.dtc import DtcStoreProvider
from ecu_simulator.scenario import Scenario, ScenarioRunner, ScenarioSync
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
        # The UDS view of this ECU's DTC state, registered on the provider registry UDS
        # data services read through (plan rule 6). OBD reads the same store directly.
        ecu.dtc_providers.register(DtcStoreProvider(ecu_name, ecu.dtc_store))
        for protocol_name in sorted({p for endpoint in ecu_config.endpoints for p in endpoint.protocols}):
            if protocol_name == ObdProtocol.name:
                ecu.register(ObdProtocol(vehicle, ecu_name=ecu_config.name, dtcs=ecu.dtc_store))
            elif protocol_name == UdsProtocol.name:
                ecu.register(UdsProtocol(dtc_providers=ecu.dtc_providers, dtcs=ecu.dtc_store))
            else:  # pragma: no cover - the schema rejects unknown protocol names
                raise ValueError(f"no protocol implementation named {protocol_name!r}")
        ecus.append(ecu)
    return ecus


def build_scenario(config: RuntimeConfig) -> Scenario:
    """The profile's scenario: vehicle-wide signals, plus each ECU's timed events.

    Events keep the order the profile lists them in, per ECU and then across ECUs, which
    is the tie-break for two events sharing a time. Everything here was validated at load:
    every signal exists on this vehicle and every event names a code its ECU carries.
    """
    profile = config.profile
    events = tuple(
        (ecu_name, event) for ecu_name, ecu in profile.ecus.items() for event in ecu.dtc_events
    )
    return Scenario(signals=tuple(profile.scenario.signals), events=events)


def build_runner(config: RuntimeConfig, vehicle: VehicleState, ecus: Iterable[Ecu]) -> ScenarioRunner | None:
    """The scenario writer, or ``None`` when the profile configures no scenario.

    ``None`` is not an optimisation. A profile without a scenario gets no runner, no tick
    and no clock reading on the request path, so its behavior is what it was before this
    phase by construction rather than by a generator that happens to return a constant.
    """
    if not config.profile.has_scenario:
        return None
    return ScenarioRunner(build_scenario(config), vehicle, {ecu.name: ecu.dtc_store for ecu in ecus})


@dataclass(frozen=True, slots=True)
class Runtime:
    """Everything a running simulator is made of, assembled once from a profile.

    The composition root. It exists so that the clock has exactly one owner, the scenario
    runner exactly one instance, and a test exactly one seam: the request path and the
    periodic tick call the same ``sync``, which is what makes a timed event impossible to
    apply twice whichever of them reaches it first.
    """

    config: RuntimeConfig
    clock: Clock
    vehicle: VehicleState
    ecus: tuple[Ecu, ...]
    router: AddressRouter
    dispatcher: Dispatcher
    runner: ScenarioRunner | None
    sync: ScenarioSync | None


def build_runtime(config: RuntimeConfig, clock: Clock | None = None) -> Runtime:
    """Assemble the runtime. ``clock`` defaults to real monotonic time; tests pass their own."""
    clock = MonotonicClock() if clock is None else clock
    vehicle = build_vehicle(config)
    ecus = tuple(build_ecus(config, vehicle))
    runner = build_runner(config, vehicle, ecus)
    # The origin is read here, once, so scenario time starts at zero however the clock is
    # counting. A profile with no scenario never reads the clock at all.
    sync = ScenarioSync(runner, clock) if runner is not None else None
    router = build_router(config)
    return Runtime(
        config=config,
        clock=clock,
        vehicle=vehicle,
        ecus=ecus,
        router=router,
        dispatcher=Dispatcher(router, ecus, sync=sync),
        runner=runner,
        sync=sync,
    )


def build_dispatcher(config: RuntimeConfig) -> Dispatcher:
    return build_runtime(config).dispatcher


@contextlib.asynccontextmanager
async def scenario_tick(sync: Callable[[], None] | None, period: float) -> AsyncIterator[None]:
    """Run ``sync`` every ``period`` seconds for as long as the block lasts.

    The tick exists for one reason: a timed trouble-code event has to arrive even when no
    tester is asking anything. Signals need no tick, because every request synchronises
    before it is answered; the period only bounds how late an event can be on an idle bus.

    ``sync`` is called for its effect on domain state and never produces bytes, so a
    failure inside it is logged and the tick carries on. Losing the scenario is bad;
    losing it silently and stopping the simulator with it would be worse.

    The task is cancelled and awaited when the block exits, which the Phase 2 lifecycle
    tests then observe as a clean SIGINT and SIGTERM shutdown with nothing left running.
    """
    if sync is None:
        yield
        return

    async def tick() -> None:
        while True:
            await asyncio.sleep(period)
            try:
                sync()
            except Exception:  # noqa: BLE001 - the simulator keeps serving; see above
                logger.exception("scenario tick failed; the simulator continues")

    task = asyncio.create_task(tick(), name="scenario-tick")
    logger.info("scenario tick every %.3gs", period)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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
    clock: Clock | None = None,
) -> None:
    """Start the transport, wait for ``stop`` (or SIGINT/SIGTERM), then shut down.

    Raises :class:`TransportError` if the transport cannot start; the caller reports it.
    """
    endpoints = build_endpoints(config)
    runtime = build_runtime(config, clock)
    check_routes(runtime.router, endpoints)  # before any socket is opened
    transport = transport_factory(config.interface, endpoints)
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    started = False
    try:
        await transport.start(runtime.dispatcher)
        started = True
        if install_signal_handlers:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _request_stop, stop, sig)
                installed.append(sig)
        logger.info("ecu-simulator ready on %s", config.interface)
        async with scenario_tick(runtime.sync, config.profile.scenario.tick):
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
