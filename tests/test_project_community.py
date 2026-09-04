from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
ISSUE_TEMPLATES = REPOSITORY / ".github" / "ISSUE_TEMPLATE"


def test_community_health_files_exist() -> None:
    required = (
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "GOVERNANCE.md",
        "SECURITY.md",
        "SUPPORT.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
    )

    missing = [path for path in required if not (REPOSITORY / path).is_file()]
    assert not missing, f"missing community health files: {missing}"


def test_security_reports_use_a_private_channel() -> None:
    security = (REPOSITORY / "SECURITY.md").read_text(encoding="utf-8")
    issue_config = (ISSUE_TEMPLATES / "config.yml").read_text(encoding="utf-8")
    advisory_url = "https://github.com/SharkFury/SharkRail/security/advisories/new"

    assert advisory_url in security
    assert advisory_url in issue_config
    assert "Do not open a public issue" in security


def test_issue_forms_require_actionable_context() -> None:
    bug_report = (ISSUE_TEMPLATES / "bug_report.yml").read_text(encoding="utf-8")
    feature_request = (ISSUE_TEMPLATES / "feature_request.yml").read_text(
        encoding="utf-8"
    )

    for field in ("version", "environment", "reproduction", "expected", "actual"):
        assert f"id: {field}" in bug_report
    for field in (
        "problem",
        "outcome",
        "beneficiary",
        "alternatives",
        "platforms",
        "contract",
        "placement",
    ):
        assert f"id: {field}" in feature_request
