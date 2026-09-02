import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sharkrail.telemetry import (
    configure_logging,
    configure_opentelemetry,
    observe_session,
)


def test_structured_logger_emits_json_without_implicit_sensitive_data() -> None:
    destination = io.StringIO()
    logger = configure_logging("INFO", destination)

    logger.info(
        "session.completed",
        extra={"fields": {"session_id": "session-1", "reason": "success"}},
    )

    payload = json.loads(destination.getvalue())
    assert payload["event"] == "session.completed"
    assert payload["session_id"] == "session-1"
    assert "argv" not in payload
    assert "env" not in payload
    assert "+00:00" in payload["timestamp"]
    configure_logging("WARNING")


def test_optional_opentelemetry_uses_host_providers() -> None:
    counter = Mock()
    histogram = Mock()
    span = Mock()
    tracer = Mock()
    tracer.start_span.return_value = span
    meter = Mock()
    meter.create_counter.return_value = counter
    meter.create_histogram.return_value = histogram
    module = SimpleNamespace(
        metrics=SimpleNamespace(get_meter=Mock(return_value=meter)),
        trace=SimpleNamespace(get_tracer=Mock(return_value=tracer)),
    )

    with patch.dict(sys.modules, {"opentelemetry": module}):
        assert configure_opentelemetry() is True
        observe_session(
            reason="success",
            duration_ms=12.5,
            drain_duration_ms=1.5,
            output_bytes=10,
            dropped_bytes=2,
        )

    counter.add.assert_any_call(1, {"sharkrail.completion.reason": "success"})
    histogram.record.assert_any_call(12.5, {"sharkrail.completion.reason": "success"})
    span.end.assert_called_once_with()
    configure_opentelemetry(False)
