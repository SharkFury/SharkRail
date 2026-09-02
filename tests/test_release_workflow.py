import subprocess
import sys
from pathlib import Path

from sharkrail import __version__

REPOSITORY = Path(__file__).resolve().parents[1]
CHECK_RELEASE = REPOSITORY / ".github" / "scripts" / "check_release.py"
RELEASE_WORKFLOW = REPOSITORY / ".github" / "workflows" / "release.yml"


def run_check(tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK_RELEASE), tag],
        cwd=REPOSITORY,
        capture_output=True,
        check=False,
        text=True,
    )


def test_current_release_tag_matches_package_version() -> None:
    result = run_check(f"v{__version__}")

    assert result.returncode == 0
    assert f"matches SharkRail {__version__}" in result.stdout


def test_release_tag_must_match_package_version() -> None:
    result = run_check("v9.9.9")

    assert result.returncode != 0
    assert "does not match package version" in result.stderr


def test_release_tag_must_use_strict_semver() -> None:
    result = run_check("release-0.1.0")

    assert result.returncode != 0
    assert "vMAJOR.MINOR.PATCH" in result.stderr


def test_release_workflow_uses_trusted_publishing_and_version_guard() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert 'tags:\n      - "v[0-9]*.[0-9]*.[0-9]*"' in workflow
    assert 'if: startsWith(github.ref, \'refs/tags/\')' in workflow
    assert 'python .github/scripts/check_release.py "${GITHUB_REF_NAME}"' in workflow
    assert "environment:\n      name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "gh release create" in workflow
