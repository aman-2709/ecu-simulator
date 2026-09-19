"""Runtime assembly from a validated profile: endpoints, routes, ECUs, vehicle, lifecycle."""

import asyncio
import logging

import pytest

from ecu_simulator import app, cli
from ecu_simulator.config import load_profile, parse_profile
from ecu_simulator.ecu import AddressRouter
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse, InterfaceNotFoundError


def shipped():
    return app.RuntimeConfig.build(load_profile(cli.default_profile_path()))


def with_interface(interface):
    return app.RuntimeConfig.build(load_profile(cli.default_profile_path()), interface)


# --- configuration ------------------------------------------------------------------------


def test_the_profile_supplies_the_interface_and_the_command_line_overrides_it():
    assert shipped().interface == "vcan0"
    assert with_interface("can0").interface == "can0"


def test_the_shipped_profile_reproduces_the_legacy_addresses_and_plus_eight_rule():
    endpoints = {e.name: e for e in app.build_endpoints(shipped())}
    assert set(endpoints) == {"engine.obd_functional", "engine.obd_physical", "engine.uds_physical"}
    functional = endpoints["engine.obd_functional"]
    physical = endpoints["engine.obd_physical"]
    uds = endpoints["engine.uds_physical"]
    assert (functional.address.rx_id, functional.address.tx_id) == (0x7DF, 0x7E8)
    assert (physical.address.rx_id, physical.address.tx_id) == (0x7E0, 0x7E8)
    assert (uds.address.rx_id, uds.address.tx_id) == (0x7E1, 0x7E9)
    assert functional.functional is True and functional.reply_via == "engine.obd_physical"
    assert physical.receive is True and uds.receive is True
    # DEV-08 corrected for OBD: padded to DLC 8; UDS unpadded, as before.
    assert functional.options.tx_padding is True and functional.options.pad_byte == 0x00
    assert physical.options.tx_padding is True
    assert uds.options.tx_padding is False


def test_padding_is_configurable_per_endpoint(tmp_path):
    text = cli.default_profile_path().read_text().replace("tx_padding: true", "tx_padding: false")
    path = tmp_path / "p.yaml"
    path.write_text(text)
    endpoints = {e.name: e for e in app.build_endpoints(app.RuntimeConfig.build(load_profile(path)))}
    assert endpoints["engine.obd_physical"].options.tx_padding is False


# --- router -------------------------------------------------------------------------------


def test_every_profile_address_routes_to_its_ecu():
    router = app.build_router(shipped())
    assert {a: r.ecu for a, r in router.physical_routes.items()} == {0x7E0: "engine", 0x7E1: "engine"}
    assert {a: tuple(r.ecu for r in rs) for a, rs in router.functional_routes.items()} == {0x7DF: ("engine",)}


def test_the_obd_broadcast_route_enables_obd_only_and_stays_silent_on_unknown_services():
    # UDS is not eligible on 0x7DF, so a UDS request there reaches no protocol at all.
    (broadcast,) = app.build_router(shipped()).functional_routes[0x7DF]
    assert broadcast.protocols == frozenset({"obd"})
    assert broadcast.answer_unsupported is False


def test_both_physical_routes_enable_obd_and_uds_and_answer_unknown_services():
    router = app.build_router(shipped())
    for address in (0x7E0, 0x7E1):
        route = router.physical_routes[address]
        assert route.protocols == frozenset({"obd", "uds"}), address
        assert route.answer_unsupported is True, address


def test_router_addresses_match_the_endpoints_that_receive():
    config = shipped()
    router = app.build_router(config)
    receiving = {e.address.rx_id: e.functional for e in app.build_endpoints(config) if e.receive}
    routed = {a: False for a in router.physical_routes} | {a: True for a in router.functional_routes}
    assert routed == receiving


# --- route and endpoint consistency ----------------------------------------------------------


def test_check_routes_accepts_the_shipped_profile():
    config = shipped()
    app.check_routes(app.build_router(config), app.build_endpoints(config))  # must not raise


def test_a_receiving_endpoint_without_a_route_is_rejected_at_startup():
    # Otherwise the socket is open and every request on it is silently dropped.
    config = shipped()
    router = AddressRouter()
    router.add_functional(0x7DF, "engine", ("obd",))
    router.add_physical(0x7E0, "engine", ("obd", "uds"))
    with pytest.raises(ValueError, match="0x7E1"):
        app.check_routes(router, app.build_endpoints(config))


def test_a_route_without_a_receiving_endpoint_is_rejected_at_startup():
    config = shipped()
    router = app.build_router(config)
    router.add_physical(0x7E5, "engine", ("obd",))
    with pytest.raises(ValueError, match="0x7E5"):
        app.check_routes(router, app.build_endpoints(config))


def test_a_route_whose_addressing_kind_differs_from_the_endpoint_is_rejected():
    config = shipped()
    router = AddressRouter()
    router.add_physical(0x7DF, "engine", ("obd",))  # 0x7DF is the functional endpoint
    router.add_physical(0x7E0, "engine", ("obd", "uds"))
    router.add_physical(0x7E1, "engine", ("obd", "uds"))
    with pytest.raises(ValueError, match="0x7DF"):
        app.check_routes(router, app.build_endpoints(config))


# --- ECUs and vehicle --------------------------------------------------------------------------


def test_each_profile_ecu_becomes_an_ecu_with_the_protocols_its_endpoints_reference():
    (engine,) = app.build_ecus(shipped())
    assert engine.name == "engine"
    assert sorted(p.name for p in engine.protocols) == ["obd", "uds"]
    assert engine.service_ids[0x01] == "obd" and engine.service_ids[0x10] == "uds"


def test_an_ecu_registers_only_the_protocols_its_endpoints_enable(tmp_path):
    text = cli.default_profile_path().read_text().replace("protocols: [obd, uds]", "protocols: [obd]")
    path = tmp_path / "obd_only.yaml"
    path.write_text(text)
    (engine,) = app.build_ecus(app.RuntimeConfig.build(load_profile(path)))
    assert [p.name for p in engine.protocols] == ["obd"]


def test_the_vehicle_is_composed_from_the_profile():
    vehicle = app.build_vehicle(shipped())
    assert vehicle.powertrain.kind == "ice"
    assert vehicle.get("vehicle.vin") == "TESTVIN0123456789"
    assert vehicle.get("engine.fuel_level") == 50
    assert vehicle.get("engine.fuel_type") == 1
    assert not vehicle.has("battery.soc")


def test_a_bev_profile_composes_a_battery_powertrain():
    data = parse_profile(
        {
            "version": 1,
            "transport": {"interface": "vcan0"},
            "vehicle": {"vin": "BEVVIN00000000001", "type": "bev", "battery": {"soc": 80.0}},
            "ecus": {
                "engine": {
                    "name": "EV_ECU",
                    "endpoints": [
                        {"name": "p", "rx": 0x7E0, "tx": 0x7E8, "addressing": "physical", "protocols": ["uds"]}
                    ],
                }
            },
        }
    )
    vehicle = app.build_vehicle(app.RuntimeConfig.build(data))
    assert vehicle.powertrain.kind == "bev"
    assert vehicle.get("battery.soc") == 80.0
    assert not vehicle.has("engine.rpm")


def test_the_legacy_uds_module_is_configured_from_the_profile(tmp_path):
    from ecu_simulator.uds import services

    text = cli.default_profile_path().read_text().replace("- B1477", "- P0100")
    path = tmp_path / "p.yaml"
    path.write_text(text)
    saved_source, saved_dtcs = services.source, services.DTCS
    try:
        app.configure_legacy_modules(app.RuntimeConfig.build(load_profile(path)))
        assert services.DTCS == bytes.fromhex("0100012f" + "0001012f")
    finally:
        services.source, services.DTCS = saved_source, saved_dtcs


def test_obd_reads_the_profile_vehicle_without_any_module_global(tmp_path):
    text = cli.default_profile_path().read_text().replace("vin: TESTVIN0123456789", "vin: PROFILEVIN123456")
    path = tmp_path / "p.yaml"
    path.write_text(text)
    dispatcher = app.build_dispatcher(app.RuntimeConfig.build(load_profile(path)))
    response = dispatcher(DiagnosticRequest(b"\x09\x02", 0x7E0))
    assert response.payload == b"\x49\x02\x01" + b"\x00" + b"PROFILEVIN123456"
    # The shipped profile is unaffected: no global was mutated.
    other = app.build_dispatcher(shipped())
    assert other(DiagnosticRequest(b"\x09\x02", 0x7E0)).payload.endswith(b"TESTVIN0123456789")


# --- dispatch -----------------------------------------------------------------------------------


def test_dispatcher_answers_by_sid_on_every_enabled_route():
    config = shipped()
    endpoints = {e.name: e for e in app.build_endpoints(config)}
    dispatcher = app.build_dispatcher(config)
    obd, physical, uds = (endpoints[n] for n in ("engine.obd_functional", "engine.obd_physical", "engine.uds_physical"))
    speed = DiagnosticResponse(b"\x41\x0d\x00")
    session = DiagnosticResponse(b"\x50\x03\x00\x1e\x0b\xb8")
    assert dispatcher(DiagnosticRequest(b"\x01\x0d", 0x7DF, functional=True, context=obd)) == speed
    assert dispatcher(DiagnosticRequest(b"\x10\x03", 0x7E1, context=uds)) == session
    # Both physical routes enable OBD and UDS, so a UDS request on 0x7E0 is answered.
    assert dispatcher(DiagnosticRequest(b"\x10\x03", 0x7E0, context=physical)) == session
    # Reading twice gives the same answer: a diagnostic read observes state (DEV-09).
    assert dispatcher(DiagnosticRequest(b"\x01\x0d", 0x7E1, context=uds)) == speed
    # 0x7DF enables OBD only, so UDS is never reached there.
    assert dispatcher(DiagnosticRequest(b"\x10\x03", 0x7DF, functional=True, context=obd)) is None
    # 0x01 monitor status is deferred, so it is a genuinely unsupported parameter.
    assert dispatcher(DiagnosticRequest(b"\x01\x01", 0x7DF, functional=True, context=obd)) is None
    assert dispatcher(DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True, context=obd)).payload.hex() == "410c0c80"


def test_dispatcher_drops_requests_on_unrouted_addresses(caplog):
    dispatcher = app.build_dispatcher(shipped())
    with caplog.at_level(logging.WARNING):
        assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7E5, context=object())) is None
    assert "no ECU" in caplog.text


# --- lifecycle ------------------------------------------------------------------------------------


class RecordingTransport:
    instances: list["RecordingTransport"] = []

    def __init__(self, interface, endpoints, fail=None):
        self.interface = interface
        self.endpoints = endpoints
        self.fail = fail
        self.events: list[str] = []
        RecordingTransport.instances.append(self)

    async def start(self, handler):
        self.events.append("start")
        if self.fail:
            raise self.fail

    async def stop(self):
        self.events.append("stop")


@pytest.mark.asyncio
async def test_run_starts_waits_for_stop_and_stops_transport():
    RecordingTransport.instances.clear()
    stop = asyncio.Event()

    async def trigger():
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await app.run(shipped(), stop=stop, install_signal_handlers=False, transport_factory=RecordingTransport)
    (transport,) = RecordingTransport.instances
    assert transport.events == ["start", "stop"]
    assert transport.interface == "vcan0" and len(transport.endpoints) == 3


@pytest.mark.asyncio
async def test_run_propagates_startup_failure_after_cleanup():
    RecordingTransport.instances.clear()
    error = InterfaceNotFoundError("nope")

    def factory(interface, endpoints):
        return RecordingTransport(interface, endpoints, fail=error)

    with pytest.raises(InterfaceNotFoundError):
        await app.run(shipped(), install_signal_handlers=False, transport_factory=factory)
    assert RecordingTransport.instances[0].events == ["start", "stop"]


@pytest.mark.asyncio
async def test_failed_start_does_not_claim_shutdown_complete(caplog):
    RecordingTransport.instances.clear()

    def factory(interface, endpoints):
        return RecordingTransport(interface, endpoints, fail=InterfaceNotFoundError("nope"))

    with caplog.at_level(logging.INFO), pytest.raises(InterfaceNotFoundError):
        await app.run(shipped(), install_signal_handlers=False, transport_factory=factory)
    assert "shutdown complete" not in caplog.text


@pytest.mark.asyncio
async def test_run_rejects_inconsistent_routes_before_opening_sockets(monkeypatch):
    RecordingTransport.instances.clear()
    monkeypatch.setattr(app, "build_router", lambda config: AddressRouter())
    coro = app.run(shipped(), install_signal_handlers=False, transport_factory=RecordingTransport)
    # wait_for bounds the failure: without the check, run() would start and wait forever.
    with pytest.raises(ValueError):
        await asyncio.wait_for(coro, timeout=2.0)
    assert RecordingTransport.instances == [], "no socket may be opened when the routes are inconsistent"


def test_each_ecu_gets_a_dtc_store_built_from_its_profile_entry():
    store = app.build_ecus(shipped())[0].dtc_store
    assert store.codes == ("B1477", "P0001")
    assert tuple(s.code for s in store.confirmed) == ("B1477", "P0001")
    assert tuple(s.code for s in store.pending) == ("B1477", "P0001")
    assert store.indicator_on is False
