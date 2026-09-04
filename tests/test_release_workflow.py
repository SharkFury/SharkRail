import re
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


def test_release_workflow_publishes_github_release_without_pypi() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert 'tags:\n      - "v[0-9]*.[0-9]*.[0-9]*"' in workflow
    assert "if: startsWith(github.ref, 'refs/tags/')" in workflow
    assert 'python .github/scripts/check_release.py "${GITHUB_REF_NAME}"' in workflow
    assert "publish-pypi:" not in workflow
    assert "pypa/gh-action-pypi-publish@" not in workflow
    assert "needs: [build, publish-pypi]" not in workflow
    assert "gh release create" in workflow


def test_release_retests_platforms_and_publishes_provenance() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert "needs: verify" in workflow
    assert "generate_sbom.py" in workflow
    assert "actions/attest@" in workflow
    assert "attestations: write" in workflow
    assert "release-metadata" in workflow


def test_release_actions_are_commit_pinned() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s+([^\s#]+)", workflow)

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in uses)
    assert workflow.count("persist-credentials: false") == workflow.count(
        "uses: actions/checkout@"
    )
