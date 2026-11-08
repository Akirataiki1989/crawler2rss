import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler

_LOG_DIR = os.environ.get("SCRAPER_LOG_DIR", "/volume1/web/codeserver/logs")
_LOG_FILE = os.path.join(_LOG_DIR, "scraper.log")


def _resolve_log_file() -> str:
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        return _LOG_FILE
    except OSError as e:
        fallback = os.path.join(os.path.dirname(__file__), "..", "logs")
        os.makedirs(fallback, exist_ok=True)
        print(f"Warning: cannot access {_LOG_DIR}, using {fallback}. ({e})")
        return os.path.join(fallback, "scraper.log")


class BracketFormatter(logging.Formatter):
    """
    Output: [2026-04-25 12:34:56.789][INFO][Scrapers][HobbySearch] message
    Logger name uses dot notation to define brackets: "Scrapers.HobbySearch"
    """

    def format(self, record: logging.LogRecord) -> str:
        ct = self.converter(record.created)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", ct)
        time_part = f"[{timestamp}.{int(record.msecs):03d}]"
        level_part = f"[{record.levelname}]"
        name_part = "".join(f"[{seg}]" for seg in record.name.split("."))

        output = f"{time_part}{level_part}{name_part} {record.getMessage()}"

        if record.exc_info:
            output += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            output += "\n" + self.formatStack(record.stack_info)

        return output


_log_file = _resolve_log_file()
_formatter = BracketFormatter()


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    file_handler = TimedRotatingFileHandler(
        _log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(_formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger
