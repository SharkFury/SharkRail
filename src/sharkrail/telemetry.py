"""Small dependency-free structured logging surface for the local runtime."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional, TextIO


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(
    level: Optional[str] = None,
    stream: Optional[TextIO] = None,
) -> logging.Logger:
    """Configure SharkRail JSON logs; output always defaults to stderr."""
    logger = logging.getLogger("sharkrail.runtime")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel((level or os.environ.get("SHARKRAIL_LOG_LEVEL", "WARNING")).upper())
    logger.propagate = False
    return logger


LOGGER = configure_logging()


def log_event(level: int, event: str, **fields: Any) -> None:
    """Emit a stable event without command arguments or environment values."""
    LOGGER.log(level, event, extra={"fields": fields})
