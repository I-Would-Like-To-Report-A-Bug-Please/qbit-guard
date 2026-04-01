import os
import sys
from loguru import logger as _logger

DETAILED_LEVEL = 15
_CONFIGURED = False


class AppLogger:
    """Small adapter so existing %-style log calls keep working on top of loguru."""

    def __init__(self, name: str):
        self._logger = _logger.bind(component=name)

    @staticmethod
    def _message(message, args):
        if not args:
            return str(message)
        try:
            return str(message) % args
        except Exception:
            return " ".join([str(message), *[str(a) for a in args]])

    def debug(self, message, *args):
        self._logger.debug(self._message(message, args))

    def info(self, message, *args):
        self._logger.info(self._message(message, args))

    def warning(self, message, *args):
        self._logger.warning(self._message(message, args))

    def error(self, message, *args):
        self._logger.error(self._message(message, args))

    def critical(self, message, *args):
        self._logger.critical(self._message(message, args))

    def detailed(self, message, *args):
        self._logger.log("DETAILED", self._message(message, args))


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    effective_level = "DETAILED" if log_level == "DETAILED" else log_level

    _logger.remove()
    try:
        _logger.level("DETAILED")
    except ValueError:
        _logger.level("DETAILED", no=DETAILED_LEVEL)
    _logger.add(
        sys.stdout,
        level=effective_level,
        format="{time:YYYY-MM-DD HH:mm:ss,SSS} | {level} | {message}",
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )
    _CONFIGURED = True


def get_logger(name: str) -> AppLogger:
    setup_logging()
    return AppLogger(name)
