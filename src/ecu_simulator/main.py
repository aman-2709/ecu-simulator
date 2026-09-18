import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from threading import Thread

from ecu_simulator import ecu_config
from ecu_simulator.loggers import logger_app, logger_can, logger_isotp
from ecu_simulator.obd import listener as obd_listener
from ecu_simulator.uds import listener as uds_listener

SETUP_VCAN_FILE = "setup_vcan.sh"

SETUP_CAN_FILE = "setup_can.sh"


def package_version():
    try:
        return version("ecu-simulator")
    except PackageNotFoundError:
        return "unknown"


def parse_args(argv=None):
    # Phase 1 exposes only --help and --version. All behavior is still driven by
    # ecu_config.json; runtime options arrive with the new CLI in Phase 2.
    parser = argparse.ArgumentParser(
        prog="ecu-simulator",
        description="Vehicle diagnostic ECU simulator (OBD-II and UDS over ISO-TP on SocketCAN). "
        "Configuration is read from ecu_config.json inside the package.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    return parser.parse_args(argv)


def main(argv=None):
    parse_args(argv)
    logger_app.configure()
    logger_app.logger.info("Starting ECU-Simulator")
    set_up_can_interface()
    star_can_logger_thread()
    star_isotp_logger_thread()
    start_obd_listener_thread()
    start_uds_listener_thread()


def set_up_can_interface():
    interface_type = ecu_config.get_can_interface_type()
    can_interface = ecu_config.get_can_interface()
    isotp_ko_file_path = ecu_config.get_isotp_ko_file_path()
    if interface_type == "virtual":
        logger_app.logger.info("Setting up virtual CAN interface: " + can_interface)
        os.system("sh " + SETUP_VCAN_FILE + " " + can_interface + " " + isotp_ko_file_path)
    elif interface_type == "hardware":
        logger_app.logger.info("Setting up CAN interface: " + can_interface)
        logger_app.logger.info("Loading ISO-TP module from: " + isotp_ko_file_path)
        os.system("sh " + SETUP_CAN_FILE + " " + can_interface + " " + ecu_config.get_can_bitrate() + " " + isotp_ko_file_path)


def star_can_logger_thread():
    Thread(target=logger_can.start).start()


def star_isotp_logger_thread():
    Thread(target=logger_isotp.start).start()


def start_obd_listener_thread():
    Thread(target=obd_listener.start).start()


def start_uds_listener_thread():
    Thread(target=uds_listener.start).start()


if __name__ == '__main__':
    sys.exit(main())
