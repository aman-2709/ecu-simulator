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
import math
import signal
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ecu_simulator.api.options import ApiOptions, ApiStartupError
from ecu_simulator.clock import Clock, MonotonicClock
from ecu_simulator.config import ConfigError, Profile
from ecu_simulator.config.schema import (
    CorruptFaultConfig,
    DbcValue,
    DelayFaultConfig,
    DropFaultConfig,
    EcuConfig,
    EndpointConfigModel,
    FaultRuleConfig,
    NrcFaultConfig,
    OfflineFaultConfig,
    ResponsePendingFaultConfig,
    TruncateFaultConfig,
)
from ecu_simulator.dtc import DtcState, DtcStore
from ecu_simulator.ecu import AddressRouter, Dispatcher, Ecu
from ecu_simulator.faults import (
    Corrupt,
    Delay,
    Drop,
    FaultAction,
    FaultPolicy,
    FaultRule,
    NrcOverride,
    Offline,
    ResponsePending,
    Truncate,
)
from ecu_simulator.protocols.obd import ObdProtocol
from ecu_simulator.protocols.uds import SessionTiming, UdsProtocol, UdsSession
from ecu_simulator.protocols.uds.dids import SessionDidProvider, StaticDidProvider, VinDidProvider
from ecu_simulator.protocols.uds.dtc import DtcStoreProvider
from ecu_simulator.publisher import CyclicFrame, publish_frames, publish_wheels, wire_id
from ecu_simulator.scenario import Scenario, ScenarioRunner, ScenarioSync
from ecu_simulator.scenario.generators import (
    ConstantSignal,
    RampSignal,
    SequenceSignal,
    SignalScenario,
    SineSignal,
    SteppedSignal,
    TimelineSignal,
)
from ecu_simulator.transport import AddressingMode, TransportError
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress, IsoTpOptions, IsoTpTransport
from ecu_simulator.vehicle import POWERTRAINS, CommonState, IceState, TractionBattery, VehicleState

if TYPE_CHECKING:
    from ecu_simulator.dbc import ImportedDb
    from ecu_simulator.restbus import WheelCodec

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
                    IsoTpAddress(endpoint.rx, endpoint.tx, endpoint.mode),
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
                endpoint_name=endpoint_name(ecu_name, endpoint),
                addressing_mode=endpoint.mode,
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
        parking_brake_active=vehicle.parking_brake_active,
        auto_wipers_on=vehicle.auto_wipers_on,
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


@dataclass
class PreparedDbc:
    metadata: dict[str, Any]
    frames: list[CyclicFrame]


def _scenario_limits(generator: SignalScenario) -> tuple[float, float]:
    values: tuple[float, ...]
    if isinstance(generator, ConstantSignal):
        values = (generator.value,)
    elif isinstance(generator, RampSignal):
        values = (generator.start, generator.to)
    elif isinstance(generator, SineSignal):
        if not math.isfinite(generator.phase):
            raise ValueError(f"{generator.path} has a non-finite phase")
        values = (generator.centre - abs(generator.amplitude), generator.centre + abs(generator.amplitude))
    elif isinstance(generator, SteppedSignal):
        values = tuple(generator.values)
    elif isinstance(generator, SequenceSignal):
        values = tuple(step.value for step in generator.steps)
    else:
        assert isinstance(generator, TimelineSignal)
        values = tuple(point.value for point in generator.points)
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{generator.path} has a non-finite scenario value")
    return min(values), max(values)


def _dbc_values(sources: dict[str, DbcValue], vehicle: VehicleState) -> dict[str, float]:
    return {signal: vehicle.get(source) if isinstance(source, str) else source for signal, source in sources.items()}


def diagnostic_ids(config: RuntimeConfig) -> dict[tuple[int, bool], str]:
    """The ``(can_id, extended)`` every diagnostic endpoint receives or sends on, naming its ``ecu.endpoint``.

    A new dict on each call, so a caller may add its own frames to it.
    """
    return {
        (can_id, endpoint.mode is not AddressingMode.NORMAL_11BIT): endpoint_name(ecu_name, endpoint)
        for ecu_name, ecu in config.profile.ecus.items() for endpoint in ecu.endpoints
        for can_id in (endpoint.rx, endpoint.tx)
    }


def prepare_dbc(
    config: RuntimeConfig, vehicle: VehicleState | None = None, *, database: ImportedDb | None = None,
    sync: Callable[[], None] | None = None,
) -> PreparedDbc | None:
    """Resolve the selected bytes and validate every cyclic frame before opening sockets."""
    selection = config.profile.dbc
    if selection is None:
        return None
    if database is None:
        from ecu_simulator.dbc import DbcStore

        try:
            database = DbcStore().load(selection.sha256)
        except (ValueError, OSError) as error:
            raise ConfigError(f"dbc {selection.filename!r}: {error}") from error
    vehicle = build_vehicle(config) if vehicle is None else vehicle
    generators = {signal.path: signal for signal in config.profile.scenario.signals}
    occupied = diagnostic_ids(config)
    frames = []
    messages = []
    for selected in selection.messages:
        try:
            message = database.message(selected.name)
            if message.get("tx_issue"):
                raise ValueError(message["tx_issue"])
            key = (message["id"], message["extended"])
            if key in occupied:
                raise ValueError(f"conflicts with {occupied[key]} on 0x{key[0]:X}")
            occupied[key] = selected.name
            initial = _dbc_values(selected.values, vehicle)
            for signal, source in selected.values.items():
                if isinstance(source, str) and source in generators:
                    first = generators[source].value_at(0)
                    if not math.isfinite(first):
                        raise ValueError(f"{source} has a non-finite scenario value")
                    initial[signal] = int(first) if isinstance(initial[signal], int) else first
            database.encode(selected.name, initial)
            selectors = {field["name"] for field in message["signals"] if field.get("is_mux")}
            for signal, source in selected.values.items():
                if signal in selectors and isinstance(source, str):
                    raise ValueError(f"multiplexer selector {signal} requires an integer constant")
                if isinstance(source, str) and source in generators:
                    low, high = _scenario_limits(generators[source])
                    integral = isinstance(initial[signal], int)
                    for limit in (low, high):
                        database.encode(selected.name, {**initial, signal: int(limit) if integral else limit})

            def sample(name: str = selected.name, sources: dict[str, DbcValue] = selected.values) -> bytes:
                if sync is not None:
                    sync()
                return database.encode(name, _dbc_values(sources, vehicle))

            frames.append(CyclicFrame(selected.name, wire_id(*key), selected.period_ms / 1000, sample))
            messages.append({**selected.model_dump(), "id": key[0], "extended": key[1], "dlc": message["dlc"]})
        except (KeyError, ValueError, OverflowError) as error:
            raise ConfigError(f"dbc message {selected.name!r}: {error}") from error
    metadata = {"filename": selection.filename, "sha256": selection.sha256, "messages": messages}
    return PreparedDbc(metadata, frames)


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
            snapshot=(dtc.snapshot.did, bytes(dtc.snapshot.data)) if dtc.snapshot else None,
            extended_record=bytes(dtc.extended_record) if dtc.extended_record else None,
            records_stored=dtc.pending or dtc.confirmed,
        )
        for dtc in ecu_config.dtcs
    )


def build_uds_session(ecu_config: EcuConfig, clock: Clock) -> UdsSession:
    """The ECU's session state, from its validated ``uds`` configuration."""
    return UdsSession(
        {entry.session: SessionTiming(entry.p2_ms, entry.p2_star_ms) for entry in ecu_config.uds.sessions},
        s3_ms=ecu_config.uds.s3_ms,
        clock=clock,
    )


def build_fault_policy(ecu_config: EcuConfig) -> FaultPolicy:
    """Build this ECU's ordered deterministic fault rules; absent means exact pass-through."""
    rules: list[FaultRule] = []
    for config in ecu_config.faults:
        action = _fault_action(config)
        rules.append(
            FaultRule(
                action,
                sid=config.sid,
                functional=config.functional_filter,
                occurrences=config.occurrence_set,
            )
        )
    return FaultPolicy(rules)


def _fault_action(config: FaultRuleConfig) -> FaultAction:
    if isinstance(config, DropFaultConfig):
        return Drop()
    if isinstance(config, DelayFaultConfig):
        return Delay(config.delay_ms / 1000)
    if isinstance(config, NrcFaultConfig):
        return NrcOverride(config.nrc)
    if isinstance(config, ResponsePendingFaultConfig):
        return ResponsePending(config.final_delay_ms / 1000)
    if isinstance(config, TruncateFaultConfig):
        return Truncate(config.remove)
    if isinstance(config, CorruptFaultConfig):
        return Corrupt(config.offset, config.xor)
    if isinstance(config, OfflineFaultConfig):
        return Offline()
    raise AssertionError(f"unhandled fault config {config!r}")


def build_ecus(config: RuntimeConfig, vehicle: VehicleState | None = None, clock: Clock | None = None) -> list[Ecu]:
    """One Ecu per profile entry, with the protocols its endpoints reference registered.

    Every ECU observes the same vehicle, so two ECUs report the same speed. ``clock`` is the
    UDS session's time source for S3; it defaults to real monotonic time.
    """
    vehicle = build_vehicle(config) if vehicle is None else vehicle
    clock = MonotonicClock() if clock is None else clock
    ecus: list[Ecu] = []
    for ecu_name, ecu_config in config.profile.ecus.items():
        ecu = Ecu(
            ecu_name,
            dtc_store=build_dtc_store(ecu_config),
            fault_policy=build_fault_policy(ecu_config),
        )
        # The UDS view of this ECU's DTC state, registered on the provider registry UDS
        # data services read through (plan rule 6). OBD reads the same store directly.
        ecu.dtc_providers.register(DtcStoreProvider(ecu_name, ecu.dtc_store))
        for protocol_name in sorted({p for endpoint in ecu_config.endpoints for p in endpoint.protocols}):
            if protocol_name == ObdProtocol.name:
                ecu.register(ObdProtocol(vehicle, ecu_name=ecu_config.name, dtcs=ecu.dtc_store))
            elif protocol_name == UdsProtocol.name:
                session = build_uds_session(ecu_config, clock)
                # The DIDs 0x22 serves: F186 and F190 built in, then the profile's own,
                # which the schema keeps off the built-in identifiers.
                ecu.dids.register(SessionDidProvider(session))
                ecu.dids.register(VinDidProvider(vehicle))
                if ecu_config.dids:
                    ecu.dids.register(StaticDidProvider(f"{ecu_name}.profile", ecu_config.did_data))
                ecu.register(
                    UdsProtocol(
                        dtc_providers=ecu.dtc_providers,
                        dtcs=ecu.dtc_store,
                        session=session,
                        dids=ecu.dids,
                        bench_output=ecu_config.uds.bench_output,
                        vehicle=vehicle,
                        clock=clock,
                    )
                )
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
    and no scenario clock reading on the request path, so its behavior is what it was before this
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
    ecus = tuple(build_ecus(config, vehicle, clock))
    runner = build_runner(config, vehicle, ecus)
    # The origin is read here, once, so scenario time starts at zero however the clock is
    # counting. A profile with no scenario never reads the clock for the scenario; an ECU
    # serving UDS reads it on every UDS request, for S3 (Phase 11).
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
    listening = {
        endpoint.name: (endpoint.address.rx_id, endpoint.address.mode, bool(endpoint.functional))
        for endpoint in endpoints
        if endpoint.receive
    }
    routed: dict[str, tuple[int, AddressingMode, bool]] = {}
    unnamed: list[int] = []
    for address, route in router.physical_routes.items():
        if route.endpoint_name is None:
            unnamed.append(address)
        else:
            routed[route.endpoint_name] = (address, route.addressing_mode, False)
    for address, routes in router.functional_routes.items():
        for route in routes:
            if route.endpoint_name is None:
                unnamed.append(address)
            else:
                routed[route.endpoint_name] = (address, route.addressing_mode, True)
    if unnamed:
        rendered = ", ".join(f"0x{address:X}" for address in sorted(unnamed))
        raise ValueError(f"route(s) {rendered} have no endpoint identity")
    for name, (address, mode, functional) in sorted(listening.items()):
        if name not in routed:
            raise ValueError(f"endpoint {name!r} on 0x{address:X} has no route")
        if routed[name] != (address, mode, functional):
            route_address, route_mode, route_functional = routed[name]
            raise ValueError(
                f"endpoint {name!r} ({mode}, 0x{address:X}, functional={functional}) "
                f"does not match its route ({route_mode}, 0x{route_address:X}, "
                f"functional={route_functional})"
            )
    for name, (address, _mode, _functional) in sorted(routed.items()):
        if name not in listening:
            raise ValueError(f"route {name!r} for address 0x{address:X} has no receiving endpoint")


def _endpoint_named(ecu: EcuConfig, name: str) -> EndpointConfigModel:
    for endpoint in ecu.endpoints:
        if endpoint.name == name:
            return endpoint
    raise ValueError(f"endpoint {name!r} is not defined on this ECU")  # unreachable: schema validated


def _api_server(runtime: Runtime, endpoints: list[EndpointConfig], api: ApiOptions) -> Any:
    try:
        from ecu_simulator.api.server import ApiServer  # the only aiohttp import (decisions/0010 §4.1)
    except ImportError as error:
        raise ApiStartupError("--api needs the optional [gui] extra: pip install 'ecu-simulator[gui]'") from error
    return ApiServer(runtime, endpoints, api)


async def run(
    config: RuntimeConfig,
    *,
    stop: asyncio.Event | None = None,
    install_signal_handlers: bool = True,
    transport_factory: Callable[..., IsoTpTransport] = IsoTpTransport,
    clock: Clock | None = None,
    api: ApiOptions | None = None,
    ready: Callable[[], None] | None = None,
    broadcast_paused: Callable[[], bool] | None = None,
    wheel_codec: WheelCodec | None = None,
    dbc_ready: Callable[[dict[str, Any]], None] | None = None,
    car_ready: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Start the transport, wait for ``stop`` (or SIGINT/SIGTERM), then shut down.

    A profile with a ``car`` publishes the car's frames; ``car_ready`` receives its metadata when it is ready.

    Raises :class:`TransportError` if the transport cannot start; the caller reports it.
    Raises :class:`ApiStartupError` if ``api`` is given and the observer API cannot start.
    """
    endpoints = build_endpoints(config)
    runtime = build_runtime(config, clock)
    check_routes(runtime.router, endpoints)  # before any socket is opened
    dbc = prepare_dbc(config, runtime.vehicle, sync=runtime.sync)
    car = None
    if config.profile.car is not None:
        from ecu_simulator.car.run import prepare_car  # car.run imports this module

        car = prepare_car(config, runtime.vehicle, runtime.clock, sync=runtime.sync)
    restbus = config.profile.restbus
    if restbus is not None:
        if wheel_codec is None:
            from ecu_simulator.restbus import WheelCodec

            wheel_codec = WheelCodec()
        logger.info("wheel-speed publisher database %s", wheel_codec.metadata())
    server = _api_server(runtime, endpoints, api) if api is not None else None
    handler = server.handler if server is not None else runtime.dispatcher
    # Constructed before the API starts, so a constructor error leaves nothing to clean up;
    # the constructor opens no socket, so a busy port still fails before any CAN socket opens.
    transport = transport_factory(config.interface, endpoints)
    if server is not None:
        await server.start()           # a busy port fails here, before transport.start()
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    started = False
    try:
        await transport.start(handler)
        started = True
        if install_signal_handlers:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _request_stop, stop, sig)
                installed.append(sig)
        uds_protocols = [
            protocol for ecu in runtime.ecus for protocol in ecu.protocols if isinstance(protocol, UdsProtocol)
        ]

        def publish_periodic() -> None:
            for protocol in uds_protocols:
                if protocol.controls.periodic or not protocol.dtcs.setting_enabled:
                    protocol.expire_session()
                for endpoint, payload in protocol.controls.reports():
                    transport.publish_periodic(endpoint, payload)

        def announce_ready() -> None:
            logger.info("ecu-simulator ready on %s", config.interface)
            if dbc is not None and dbc_ready is not None:
                dbc_ready(dbc.metadata)
            if car is not None and car_ready is not None:
                car_ready(car.metadata)
            if ready is not None:
                ready()

        async with scenario_tick(runtime.sync, config.profile.scenario.tick), scenario_tick(publish_periodic, 0.05):
            if car is not None:
                await publish_frames(config.interface, car.frames, broadcast_paused, announce_ready, stop)
            elif dbc is not None and dbc.frames:
                await publish_frames(config.interface, dbc.frames, broadcast_paused, announce_ready, stop)
            elif restbus is None:
                announce_ready()
                await stop.wait()
            else:
                assert wheel_codec is not None
                encode = wheel_codec.encode

                def sample() -> bytes:
                    if runtime.sync is not None:
                        runtime.sync()
                    return encode(runtime.vehicle.common.speed)

                await publish_wheels(
                    config.interface, restbus.period_ms / 1000, sample, broadcast_paused, announce_ready, stop
                )
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)
        if server is not None:
            await server.stop()
        await transport.stop()
        if started:
            logger.info("shutdown complete")


def _request_stop(stop: asyncio.Event, sig: signal.Signals) -> None:
    logger.info("received %s, shutting down", sig.name)
    stop.set()


def main(config: RuntimeConfig, api: ApiOptions | None = None) -> int:
    """Run to completion; exit status 0 on clean shutdown, 2 on a transport/startup failure."""
    try:
        asyncio.run(run(config, api=api))
    except (TransportError, ApiStartupError, ConfigError) as error:
        logger.error("%s", error)
        return 2
    return 0
