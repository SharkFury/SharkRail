"""Small dependency-free structured logging surface for the local runtime."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

_OTEL: Optional[dict[str, Any]] = None


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


def configure_opentelemetry(enabled: bool = True) -> bool:
    """Use the host application's configured OpenTelemetry providers when installed."""
    global _OTEL
    if not enabled:
        _OTEL = None
        return False
    try:
        from opentelemetry import metrics, trace
    except ImportError:
        return False

    meter = metrics.get_meter("sharkrail")
    _OTEL = {
        "tracer": trace.get_tracer("sharkrail"),
        "sessions": meter.create_counter("sharkrail.sessions.completed"),
        "duration": meter.create_histogram("sharkrail.session.duration", unit="ms"),
        "drain": meter.create_histogram("sharkrail.session.drain.duration", unit="ms"),
        "output": meter.create_counter("sharkrail.output.bytes", unit="By"),
        "dropped": meter.create_counter("sharkrail.output.dropped.bytes", unit="By"),
    }
    return True


def observe_session(
    *,
    reason: str,
    duration_ms: float,
    drain_duration_ms: float,
    output_bytes: int,
    dropped_bytes: int,
) -> None:
    if _OTEL is None:
        return
    attributes = {"sharkrail.completion.reason": reason}
    _OTEL["sessions"].add(1, attributes)
    _OTEL["duration"].record(duration_ms, attributes)
    _OTEL["drain"].record(drain_duration_ms, attributes)
    _OTEL["output"].add(output_bytes, attributes)
    _OTEL["dropped"].add(dropped_bytes, attributes)
    span = _OTEL["tracer"].start_span("sharkrail.session")
    span.set_attribute("sharkrail.completion.reason", reason)
    span.set_attribute("sharkrail.duration_ms", duration_ms)
    span.set_attribute("sharkrail.output.bytes", output_bytes)
    span.end()
