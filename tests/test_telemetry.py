import io
import json

from sharkrail.telemetry import configure_logging


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
