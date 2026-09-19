"""Command line: profile selection, validate-config, and failure exit codes."""

import logging

import pytest

from ecu_simulator import cli
from ecu_simulator.loggers import logger_app


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    """cli.main() configures the shared logger and writes ecu_simulator.log to the cwd."""
    monkeypatch.chdir(tmp_path)
    saved_level, saved_handlers = logger_app.logger.level, list(logger_app.logger.handlers)
    yield
    for handler in logger_app.logger.handlers:
        if handler not in saved_handlers:
            handler.close()
    logger_app.logger.handlers[:] = saved_handlers
    logger_app.logger.setLevel(saved_level)


def test_parser_defaults_and_options():
    args = cli.build_parser().parse_args([])
    assert args.command == "run" and args.interface is None and args.profile is None and args.log_level == "INFO"
    args = cli.build_parser().parse_args(["validate-config", "--profile", "p.yaml", "--log-level", "DEBUG"])
    assert args.command == "validate-config" and args.profile == "p.yaml" and args.log_level == "DEBUG"


def test_an_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["frobnicate"])


def test_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("ecu-simulator ")


def test_the_default_profile_ships_with_the_package():
    path = cli.default_profile_path()
    assert path.is_file() and path.name == "ice_default.yaml"


def test_validate_config_accepts_the_shipped_profile(isolated_logging, caplog):
    with caplog.at_level(logging.INFO):
        assert cli.main(["validate-config"]) == 0
    assert "is valid" in caplog.text and "ice" in caplog.text


def test_validate_config_reports_an_invalid_profile_with_exit_code_2(isolated_logging, tmp_path, caplog):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\ntransport: {interface: vcan0}\nvehicle: {vin: X, type: steam}\necus: {}\n")
    with caplog.at_level(logging.ERROR):
        assert cli.main(["validate-config", "--profile", str(bad)]) == 2
    assert "bad.yaml" in caplog.text and "type" in caplog.text


def test_a_missing_profile_is_reported_with_exit_code_2(isolated_logging, tmp_path, caplog):
    with caplog.at_level(logging.ERROR):
        assert cli.main(["--profile", str(tmp_path / "nosuch.yaml")]) == 2
    assert "nosuch.yaml" in caplog.text


def test_validate_config_never_opens_a_socket(isolated_logging, caplog):
    # A valid profile validates even when its interface does not exist.
    with caplog.at_level(logging.INFO):
        assert cli.main(["validate-config", "--interface", "nosuchcan9"]) == 0


def test_run_reports_a_missing_interface_with_exit_code_2(isolated_logging, caplog):
    # Real transport, real environment checks: either the interface is missing or the
    # kernel lacks CAN_ISOTP (GitHub-hosted runners). Both are startup failures -> 2.
    with caplog.at_level(logging.ERROR):
        assert cli.main(["--interface", "nosuchcan9", "--log-level", "ERROR"]) == 2
    assert "nosuchcan9" in caplog.text or "CONFIG_CAN_ISOTP" in caplog.text
