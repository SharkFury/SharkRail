import re
from pathlib import Path

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_ci_runs_complete_suite_on_every_matrix_member():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert 'python-version: ["3.9", "3.11", "3.14"]' in workflow
    assert "Run complete test suite" in workflow
    assert "python -m pytest --timeout=20" in workflow
    assert "runner.os != 'Windows'" not in workflow


def test_ci_enforces_types_format_coverage_and_installed_wheel():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    for gate in (
        "python -m ruff format --check .",
        "python -m mypy src/sharkrail",
        "--cov-fail-under=70",
        ".github/scripts/wheel_smoke.py",
    ):
        assert gate in workflow


def test_ci_actions_are_pinned_and_checkout_drops_credentials():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s+([^\s#]+)", workflow)

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in uses)
    assert workflow.count("persist-credentials: false") == workflow.count(
        "uses: actions/checkout@"
    )
