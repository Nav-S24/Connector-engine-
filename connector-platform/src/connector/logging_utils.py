"""Structured JSON logging utilities for connector runs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

_STANDARD_LOG_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Format Python log records as single-line structured JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize log record and extra structured fields to JSON."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str = "connector", level: int = logging.INFO) -> logging.Logger:
    """Return a logger configured with a single JsonLogFormatter stream handler."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not any(
        isinstance(getattr(h, "formatter", None), JsonLogFormatter)
        for h in logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger
