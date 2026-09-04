import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sharkrail.doctor import (
    _probe_execution,
    diagnose,
    format_report,
    write_diagnostic_bundle,
)
from sharkrail.models import CommandMode


def test_doctor_report_is_safe_and_structured():
    report = diagnose()
    payload = report.to_dict()

    assert payload["runtime_version"]
    assert payload["protocol_version"] == "1.0.0"
    assert payload["platform"] in {"windows", "linux", "macos"}
    assert isinstance(payload["checks"], tuple)
    assert "environment" not in payload
    assert "healthy" in payload


def test_doctor_human_report_contains_core_diagnostics():
    text = format_report(diagnose())
    assert "SharkRail" in text
    assert "Platform:" in text
    assert "Process tree:" in text
    assert "Checks:" in text


def test_doctor_actively_probes_pipe_execution():
    report = diagnose()
    pipe = next(check for check in report.checks if check.name == "pipe")

    assert pipe.status == "pass"
    assert "active" in pipe.detail
    assert pipe.duration_ms >= 0


def test_diagnostic_bundle_is_structured_and_secret_free(tmp_path):
    destination = tmp_path / "diagnostics.json"
    write_diagnostic_bundle(diagnose(), destination)

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0.0"
    assert payload["doctor"]["healthy"] is True
    assert "environment" not in payload
    assert "argv" not in payload


def test_failed_probe_reports_structured_completion_details():
    manager = Mock()
    manager.start = AsyncMock(return_value=SimpleNamespace(id="probe"))
    manager.wait = AsyncMock(
        return_value=SimpleNamespace(
            exit_code=1,
            stdout="sharkrail-probe True",
            reason=SimpleNamespace(value="resource_limited"),
            error=SimpleNamespace(code=SimpleNamespace(value="drain_timeout")),
        )
    )
    manager.shutdown = AsyncMock()

    with patch("sharkrail.doctor.SessionManager", return_value=manager):
        check = asyncio.run(_probe_execution(CommandMode.PIPE))

    assert check.status == "fail"
    assert "reason=resource_limited" in check.detail
    assert "error=drain_timeout" in check.detail
    manager.shutdown.assert_awaited_once_with()


def test_pty_probe_allows_for_conpty_cold_start():
    manager = Mock()
    manager.start = AsyncMock(return_value=SimpleNamespace(id="probe"))
    manager.wait = AsyncMock(
        return_value=SimpleNamespace(
            exit_code=0,
            stdout="sharkrail-probe True",
            reason=SimpleNamespace(value="success"),
            error=None,
        )
    )
    manager.shutdown = AsyncMock()

    with patch("sharkrail.doctor.SessionManager", return_value=manager):
        check = asyncio.run(_probe_execution(CommandMode.PTY))

    assert check.status == "pass"
    assert manager.start.await_args.kwargs["timeout_ms"] == 10_000
    manager.wait.assert_awaited_once_with("probe", timeout_ms=12_000)
