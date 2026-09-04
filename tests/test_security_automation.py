import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dependabot_updates_python_and_actions_dependencies():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert "package-ecosystem: pip" in config
    assert "package-ecosystem: github-actions" in config
    assert config.count("interval: weekly") == 2


def test_codeql_and_scorecard_workflows_are_scheduled_and_least_privilege():
    codeql = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
    scorecard = (ROOT / ".github" / "workflows" / "scorecard.yml").read_text(
        encoding="utf-8"
    )

    assert "schedule:" in codeql and "security-events: write" in codeql
    assert "languages: python" in codeql
    assert "permissions: read-all" in scorecard
    assert "publish_results: true" in scorecard
    assert "pull_request_target" not in codeql + scorecard


def test_security_workflow_actions_are_commit_pinned():
    workflows = [
        ROOT / ".github" / "workflows" / "codeql.yml",
        ROOT / ".github" / "workflows" / "scorecard.yml",
    ]
    uses = [
        action
        for path in workflows
        for action in re.findall(r"uses:\s+([^\s#]+)", path.read_text(encoding="utf-8"))
    ]

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in uses)
