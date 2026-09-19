"""Per-ECU / per-protocol logger context: every record made while an ECU handles a request
carries the ECU and protocol names, including records from the untouched legacy modules."""

import logging

import pytest

from ecu_simulator import logging as ecu_logging
from ecu_simulator.ecu import Ecu, Route
from ecu_simulator.loggers import logger_app
from ecu_simulator.transport import DiagnosticRequest


def test_no_context_by_default():
    assert ecu_logging.current_context() is None


def test_context_nests_and_restores():
    with ecu_logging.log_context("engine"):
        assert ecu_logging.current_context() == ecu_logging.LogContext("engine", None)
        with ecu_logging.log_context("engine", "uds"):
            assert ecu_logging.current_context() == ecu_logging.LogContext("engine", "uds")
        assert ecu_logging.current_context() == ecu_logging.LogContext("engine", None)
    assert ecu_logging.current_context() is None


def test_context_is_restored_when_the_block_raises():
    with pytest.raises(RuntimeError), ecu_logging.log_context("engine", "obd"):
        raise RuntimeError("boom")
    assert ecu_logging.current_context() is None


def make_record(message="hello"):
    return logging.LogRecord("ecu_simulator", logging.INFO, __file__, 1, message, None, None)


def test_filter_stamps_records_with_the_current_context():
    stamp = ecu_logging.ContextFilter()
    record = make_record()
    assert stamp.filter(record) is True
    assert (record.ecu, record.protocol) == ("-", "-")
    with ecu_logging.log_context("engine", "uds"):
        record = make_record()
        stamp.filter(record)
    assert (record.ecu, record.protocol) == ("engine", "uds")
    with ecu_logging.log_context("tcm"):
        record = make_record()
        stamp.filter(record)
    assert (record.ecu, record.protocol) == ("tcm", "-")


def test_format_renders_context_before_the_message():
    formatter = logging.Formatter(ecu_logging.LOG_FORMAT, datefmt=ecu_logging.DATE_FORMAT)
    record = make_record("Requested UDS SID 0x10")
    with ecu_logging.log_context("engine", "uds"):
        ecu_logging.ContextFilter().filter(record)
    line = formatter.format(record)
    assert line.endswith(" - ecu_simulator - INFO - [engine/uds] Requested UDS SID 0x10")


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved_level, saved_handlers = logger_app.logger.level, list(logger_app.logger.handlers)
    saved_filters = list(logger_app.logger.filters)
    yield
    for handler in logger_app.logger.handlers:
        if handler not in saved_handlers:
            handler.close()
    logger_app.logger.handlers[:] = saved_handlers
    logger_app.logger.filters[:] = saved_filters
    logger_app.logger.setLevel(saved_level)


def test_configure_installs_the_context_on_every_handler(isolated_logging, capsys, tmp_path):
    logger_app.configure(logging.INFO)
    with ecu_logging.log_context("engine", "obd"):
        logger_app.logger.info("Requested OBD SID 0x1")
    logger_app.logger.info("outside")
    err = capsys.readouterr().err
    assert "[engine/obd] Requested OBD SID 0x1" in err
    assert "[-/-] outside" in err
    for handler in logger_app.logger.handlers:
        handler.flush()
    assert "[engine/obd] Requested OBD SID 0x1" in (tmp_path / "ecu_simulator.log").read_text()


class Spy:
    name = "spy"
    service_ids = frozenset({0x3E})

    def __init__(self):
        self.context = None

    def handle(self, request):
        self.context = ecu_logging.current_context()
        logging.getLogger("ecu_simulator").info("legacy-style line")
        return b"\x7e\x00"


def test_ecu_handles_requests_inside_its_context(caplog):
    caplog.handler.addFilter(ecu_logging.ContextFilter())
    ecu = Ecu("engine")
    spy = Spy()
    ecu.register(spy)
    with caplog.at_level(logging.INFO):
        ecu.handle(DiagnosticRequest(b"\x3e\x00", 0x7E1), Route("engine", frozenset({"spy"})))
    assert spy.context == ecu_logging.LogContext("engine", "spy")
    assert ecu_logging.current_context() is None
    stamped = {(r.ecu, r.protocol) for r in caplog.records}
    assert ("engine", "spy") in stamped, stamped
    assert all(r.ecu == "engine" for r in caplog.records), [(r.ecu, r.getMessage()) for r in caplog.records]
