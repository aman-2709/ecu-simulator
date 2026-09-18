"""Command-line entry point: ``ecu-simulator`` and ``python -m ecu_simulator``."""

from __future__ import annotations

import argparse
import logging
import sys
from importlib.metadata import PackageNotFoundError, version

from ecu_simulator import app
from ecu_simulator.loggers import logger_app

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def package_version() -> str:
    try:
        return version("ecu-simulator")
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecu-simulator",
        description=(
            "Vehicle diagnostic ECU simulator: legacy OBD-II and UDS over kernel ISO-TP on SocketCAN. "
            "The CAN interface must exist and be up (see scripts/setup_vcan.sh and scripts/setup_can.sh); "
            "the simulator itself runs unprivileged. Addresses and vehicle data still come from the "
            "package's ecu_config.json in this release."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    parser.add_argument(
        "--interface",
        metavar="IFACE",
        help="SocketCAN interface to use, e.g. vcan0 or can0 (default: can_interface from ecu_config.json)",
    )
    parser.add_argument("--log-level", choices=LOG_LEVELS, default="INFO", help="log verbosity (default: INFO)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger_app.configure(getattr(logging, args.log_level))
    config = app.config_from_legacy(args.interface)
    try:
        return app.main(config)
    except KeyboardInterrupt:
        # Ctrl-C before the runtime installed its signal handlers (during startup).
        logging.getLogger("ecu_simulator").info("interrupted during startup")
        return 130


if __name__ == "__main__":
    sys.exit(main())
