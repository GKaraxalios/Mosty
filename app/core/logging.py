"""Central logging: console + rotating file(s), request correlation id, levels.

- Never log API keys, tokens, or passwords. Pass only flags or lengths if needed.
- Use ``get_logger(__name__)`` in every module.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from app.core.config import BASE_DIR, get_settings
from app.core.log_context import get_request_id


class RequestContextFilter(logging.Filter):
    """Attach ``request_id`` to each log record for formatter output."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = get_request_id()
        record.request_id = rid if rid else "-"
        return True


class RequestContextFormatter(logging.Formatter):
    """Formatter that always has ``request_id`` on the record (via filter)."""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return super().format(record)


def _parse_log_level(name: str | None) -> int:
    if not name or not str(name).strip():
        return logging.NOTSET
    level = getattr(logging, str(name).strip().upper(), None)
    return level if isinstance(level, int) else logging.NOTSET


def setup_logging() -> None:
    """Configure root logger: stdout + optional rotating ``logs/app.log`` + optional ``logs/errors.log``.

    Idempotent enough for tests: clears existing root handlers before attaching new ones.
    """
    settings = get_settings()

    configured = _parse_log_level(getattr(settings, "LOG_LEVEL", None))
    if configured != logging.NOTSET:
        level = configured
    else:
        level = logging.DEBUG if settings.DEBUG else logging.INFO

    fmt = (
        "[%(asctime)s] %(levelname)-8s [req=%(request_id)s] %(name)s — %(message)s"
    )
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    ctx_filter = RequestContextFilter()
    formatter = RequestContextFormatter(fmt, datefmt=datefmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(ctx_filter)
    root.addHandler(stream_handler)

    if getattr(settings, "LOG_TO_FILE", True):
        log_path = (BASE_DIR / settings.LOG_FILE_PATH).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=int(settings.LOG_FILE_MAX_BYTES),
            backupCount=int(settings.LOG_FILE_BACKUP_COUNT),
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ctx_filter)
        root.addHandler(file_handler)

    if getattr(settings, "LOG_ERROR_TO_SEPARATE_FILE", False):
        err_path = (BASE_DIR / settings.LOG_ERROR_FILE_PATH).resolve()
        err_path.parent.mkdir(parents=True, exist_ok=True)
        err_handler = logging.handlers.RotatingFileHandler(
            err_path,
            maxBytes=int(settings.LOG_ERROR_FILE_MAX_BYTES),
            backupCount=int(settings.LOG_ERROR_FILE_BACKUP_COUNT),
            encoding="utf-8",
        )
        err_handler.setLevel(logging.ERROR)
        err_handler.setFormatter(formatter)
        err_handler.addFilter(ctx_filter)
        root.addHandler(err_handler)

    # Third-party noise reduction
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
