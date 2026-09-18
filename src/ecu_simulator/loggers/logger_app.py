import logging
from logging import handlers

from ecu_simulator.logging import DATE_FORMAT, LOG_FORMAT, ContextFilter

LOGGER_NAME = "ecu_simulator"

MAX_LOG_FILE_SIZE = 1500000  # bytes per rotated ecu_simulator.log file

LOGGER_FORMAT = LOG_FORMAT

LOG_FILE_NAME = LOGGER_NAME + ".log"

logger = logging.getLogger(LOGGER_NAME)


def configure(level=logging.DEBUG):
    """Attach the rotating file handler and the console handler once, at ``level``.

    Both handlers carry the ECU/protocol context filter, so every line shows which ECU
    and protocol produced it (``[-/-]`` outside request handling).
    """
    logger.setLevel(level)
    if logger.handlers:
        for handler in logger.handlers:
            handler.setLevel(level)
        return
    formatter = logging.Formatter(LOGGER_FORMAT, datefmt=DATE_FORMAT)
    context = ContextFilter()
    __add_file_handler(formatter, level, context)
    __add_console_handler(formatter, level, context)


def __add_file_handler(formatter, level, context):
    fh = handlers.RotatingFileHandler(LOG_FILE_NAME, maxBytes=MAX_LOG_FILE_SIZE, backupCount=5)
    fh.setLevel(level)
    fh.setFormatter(formatter)
    fh.addFilter(context)
    logger.addHandler(fh)


def __add_console_handler(formatter, level, context):
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)
    ch.addFilter(context)
    logger.addHandler(ch)
