import logging
from logging import handlers

LOGGER_NAME = "ecu_simulator"

MAX_LOG_FILE_SIZE = 1500000  # bytes per rotated ecu_simulator.log file

DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

LOGGER_FORMAT = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s"

LOG_FILE_NAME = LOGGER_NAME + ".log"

logger = logging.getLogger(LOGGER_NAME)


def configure(level=logging.DEBUG):
    """Attach the rotating file handler and the console handler once, at ``level``."""
    logger.setLevel(level)
    if logger.handlers:
        for handler in logger.handlers:
            handler.setLevel(level)
        return
    formatter = logging.Formatter(LOGGER_FORMAT, datefmt=DATE_FORMAT)
    __add_file_handler(formatter, level)
    __add_console_handler(formatter, level)


def __add_file_handler(formatter, level):
    fh = handlers.RotatingFileHandler(LOG_FILE_NAME, maxBytes=MAX_LOG_FILE_SIZE, backupCount=5)
    fh.setLevel(level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)


def __add_console_handler(formatter, level):
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

