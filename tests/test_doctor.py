from sharkrail.doctor import diagnose, format_report


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
