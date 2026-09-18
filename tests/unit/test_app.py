import asyncio
import dataclasses
import logging

import pytest

from ecu_simulator import app, cli
from ecu_simulator.transport import DiagnosticRequest, DiagnosticResponse, InterfaceNotFoundError
from ecu_simulator.transport.socketcan import EndpointConfig, IsoTpAddress


def test_config_from_legacy_uses_shipped_addresses_and_plus_eight_rule():
    config = app.config_from_legacy()
    assert config.interface == "vcan0"
    assert (config.obd_functional_id, config.obd_physical_id, config.obd_response_id) == (0x7DF, 0x7E0, 0x7E8)
    assert (config.uds_request_id, config.uds_response_id) == (0x7E1, 0x7E9)
    assert app.config_from_legacy("can0").interface == "can0"


def test_build_endpoints_reproduces_legacy_sockets():
    endpoints = app.build_endpoints(app.config_from_legacy())
    by_name = {e.name: e for e in endpoints}
    assert set(by_name) == {"obd_functional", "obd_physical", "uds_physical"}
    functional, physical, uds = by_name["obd_functional"], by_name["obd_physical"], by_name["uds_physical"]
    assert functional.functional is True and functional.reply_via == "obd_physical"
    assert (functional.address.rx_id, functional.address.tx_id) == (0x7DF, 0x7E8)
    assert (physical.address.rx_id, physical.address.tx_id) == (0x7E0, 0x7E8)
    assert physical.receive is True  # DEV-01 corrected: physically addressed requests are served
    assert (uds.address.rx_id, uds.address.tx_id) == (0x7E1, 0x7E9) and uds.receive is True
    # DEV-08 corrected for OBD: padded to DLC 8 with the configured pad byte; UDS unchanged.
    assert functional.options.tx_padding is True and physical.options.tx_padding is True
    assert functional.options.pad_byte == 0x00
    assert uds.options.tx_padding is False


def test_pad_byte_and_padding_are_configurable():
    custom = dataclasses.replace(app.config_from_legacy(), pad_byte=0xAA, obd_tx_padding=False)
    endpoints = {e.name: e for e in app.build_endpoints(custom)}
    assert endpoints["obd_physical"].options == app.IsoTpOptions(tx_padding=False, pad_byte=0xAA)


def test_dispatcher_routes_by_endpoint_name_and_wraps_bytes(monkeypatch):
    from ecu_simulator.obd import responses

    monkeypatch.setattr(responses, "vehicle_speed", 0)
    endpoints = app.build_endpoints(app.config_from_legacy())
    dispatcher = app.LegacyDispatcher.for_endpoints(endpoints)
    obd, _physical, uds = endpoints
    assert dispatcher(DiagnosticRequest(b"\x01\x0d", 0x7DF, functional=True, context=obd)) == DiagnosticResponse(
        b"\x41\x0d\x00"
    )
    assert dispatcher(DiagnosticRequest(b"\x10\x03", 0x7E1, context=uds)) == DiagnosticResponse(
        b"\x50\x03\x00\x1e\x0b\xb8"
    )
    assert dispatcher(DiagnosticRequest(b"\x01\x0c", 0x7DF, functional=True, context=obd)) is None


def test_dispatcher_rejects_unknown_endpoint_kind_and_unknown_context(caplog):
    with pytest.raises(ValueError, match="no legacy handler"):
        app.LegacyDispatcher.for_endpoints([EndpointConfig("doip", IsoTpAddress(0x1, 0x2))])
    dispatcher = app.LegacyDispatcher({})
    with caplog.at_level(logging.ERROR):
        assert dispatcher(DiagnosticRequest(b"\x3e\x00", 0x7E1, context=object())) is None
    assert "no handler" in caplog.text


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
    config = app.config_from_legacy()

    async def trigger():
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await app.run(config, stop=stop, install_signal_handlers=False, transport_factory=RecordingTransport)
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
        await app.run(app.config_from_legacy(), install_signal_handlers=False, transport_factory=factory)
    assert RecordingTransport.instances[0].events == ["start", "stop"]


@pytest.mark.asyncio
async def test_failed_start_does_not_claim_shutdown_complete(caplog):
    RecordingTransport.instances.clear()

    def factory(interface, endpoints):
        return RecordingTransport(interface, endpoints, fail=InterfaceNotFoundError("nope"))

    with caplog.at_level(logging.INFO), pytest.raises(InterfaceNotFoundError):
        await app.run(app.config_from_legacy(), install_signal_handlers=False, transport_factory=factory)
    assert "shutdown complete" not in caplog.text


def test_cli_parser_defaults_and_options():
    args = cli.build_parser().parse_args([])
    assert args.interface is None and args.log_level == "INFO"
    args = cli.build_parser().parse_args(["--interface", "can0", "--log-level", "DEBUG"])
    assert args.interface == "can0" and args.log_level == "DEBUG"


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("ecu-simulator ")


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """cli.main() configures the shared 'ecu_simulator' logger and writes ecu_simulator.log to the cwd."""
    from ecu_simulator.loggers import logger_app

    monkeypatch.chdir(tmp_path)
    saved_level, saved_handlers = logger_app.logger.level, list(logger_app.logger.handlers)
    yield
    for handler in logger_app.logger.handlers:
        if handler not in saved_handlers:
            handler.close()
    logger_app.logger.handlers[:] = saved_handlers
    logger_app.logger.setLevel(saved_level)


def test_cli_reports_missing_interface_with_exit_code_2(isolated_logging, caplog):
    # Real transport, real environment checks: either the interface is missing or the
    # kernel lacks CAN_ISOTP (GitHub-hosted runners). Both are startup failures -> 2.
    with caplog.at_level(logging.ERROR):
        assert cli.main(["--interface", "nosuchcan9", "--log-level", "ERROR"]) == 2
    assert "nosuchcan9" in caplog.text or "CONFIG_CAN_ISOTP" in caplog.text
