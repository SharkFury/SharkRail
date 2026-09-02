import json

from sharkrail.doctor import diagnose, format_report, write_diagnostic_bundle


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
