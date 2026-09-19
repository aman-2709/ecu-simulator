"""Command-line entry point: ``ecu-simulator`` and ``python -m ecu_simulator``."""

from __future__ import annotations

import argparse
import logging
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ecu_simulator import app
from ecu_simulator.config import DEFAULT_PROFILE, ConfigError, load_profile
from ecu_simulator.loggers import logger_app

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
COMMANDS = ("run", "validate-config")


def package_version() -> str:
    try:
        return version("ecu-simulator")
    except PackageNotFoundError:
        return "unknown"


def default_profile_path() -> Path:
    """The shipped profile, whether running from a checkout or an installed package."""
    packaged = Path(__file__).resolve().parent / "profiles" / "ice_default.yaml"
    if packaged.is_file():
        return packaged
    return Path(DEFAULT_PROFILE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecu-simulator",
        description=(
            "Vehicle diagnostic ECU simulator: OBD-II and UDS over kernel ISO-TP on SocketCAN. "
            "The CAN interface must exist and be up (see scripts/setup_vcan.sh and scripts/setup_can.sh); "
            "the simulator itself runs unprivileged. Addresses, vehicle data and per-ECU trouble codes "
            "come from a YAML profile."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=COMMANDS,
        default="run",
        help="run the simulator (default), or validate a profile and exit",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    parser.add_argument(
        "--interface",
        metavar="IFACE",
        help="SocketCAN interface to use, e.g. vcan0 or can0 (default: the profile's transport.interface)",
    )
    parser.add_argument(
        "--profile",
        metavar="PATH",
        help="YAML profile to load (default: the packaged profiles/ice_default.yaml)",
    )
    parser.add_argument("--log-level", choices=LOG_LEVELS, default="INFO", help="log verbosity (default: INFO)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger_app.configure(getattr(logging, args.log_level))
    log = logging.getLogger("ecu_simulator")
    path = Path(args.profile) if args.profile else default_profile_path()
    try:
        profile = load_profile(path)
    except ConfigError as error:
        log.error("%s", error)
        return 2
    if args.command == "validate-config":
        ecus = ", ".join(profile.ecus)
        endpoints = sum(len(ecu.endpoints) for ecu in profile.ecus.values())
        log.info(
            "profile %s is valid: %s vehicle, ECUs [%s], %d endpoint(s)", path, profile.vehicle.type, ecus, endpoints
        )
        return 0
    config = app.RuntimeConfig.build(profile, args.interface)
    try:
        return app.main(config)
    except KeyboardInterrupt:
        # Ctrl-C before the runtime installed its signal handlers (during startup).
        log.info("interrupted during startup")
        return 130


if __name__ == "__main__":
    sys.exit(main())
