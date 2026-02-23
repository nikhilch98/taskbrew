"""Structured logging configuration for TaskBrew.

Provides two log formats:
- **dev** (default): human-readable, colourless, with timestamp/level/module.
- **json**: machine-parseable JSON lines for production / log aggregation.

Usage (at application entry point)::

    from taskbrew.logging_config import setup_logging
    setup_logging()          # dev format
    setup_logging("json")    # JSON format

All modules should obtain their logger via::

    import logging
    logger = logging.getLogger(__name__)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields: timestamp, level, logger, message, plus any *extra* keys
    attached to the record.
    """

    # Keys that belong to the standard LogRecord and should not be surfaced
    # as user-supplied *extra* fields.
    _BUILTIN_ATTRS = frozenset({
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg",
        "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "thread", "threadName", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any extra fields the caller attached to the record.
        for key, value in record.__dict__.items():
            if key not in self._BUILTIN_ATTRS and not key.startswith("_"):
                log_entry[key] = value

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Dev (human-readable) formatter
# ---------------------------------------------------------------------------

DEV_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DEV_DATEFMT = "%H:%M:%S"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(
    fmt: str | None = None,
    level: int | str | None = None,
) -> None:
    """Configure the root logger for the application.

    Parameters
    ----------
    fmt:
        ``"json"`` for structured JSON lines or ``"dev"`` (default) for a
        human-readable format.  Can also be set via the ``LOG_FORMAT``
        environment variable.
    level:
        Logging level (name or int).  Defaults to ``INFO``.  Can also be set
        via the ``LOG_LEVEL`` environment variable.
    """
    fmt = fmt or os.environ.get("LOG_FORMAT", "dev")
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        resolved = getattr(logging, level.upper(), None)
        if resolved is None:
            print(
                f"WARNING: Invalid LOG_LEVEL '{level}', falling back to INFO",
                file=sys.stderr,
            )
            resolved = logging.INFO
        level = resolved

    handler = logging.StreamHandler(sys.stderr)

    if fmt == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(DEV_FORMAT, datefmt=DEV_DATEFMT))

    root = logging.getLogger()
    # Remove any handlers that basicConfig or prior setup may have added.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quieten noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# File logging (used by background daemon)
# ---------------------------------------------------------------------------

TASKBREW_LOG = Path.home() / ".taskbrew" / "taskbrew.log"


def setup_file_logging(
    log_file: Path | None = None,
    level: int | str | None = None,
) -> None:
    """Add a rotating file handler to the root logger.

    Called by the daemon/serve process so that all output is captured to disk.
    """
    log_file = log_file or TASKBREW_LOG
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(level, str):
        resolved = getattr(logging, level.upper(), None)
        level = resolved if resolved is not None else logging.INFO

    handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(DEV_FORMAT, datefmt=DEV_DATEFMT))
    handler.setLevel(level)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quieten noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
