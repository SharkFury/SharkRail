import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE_DIRECTORY = Path("examples")
RUNNABLE_EXAMPLES = tuple(sorted(EXAMPLE_DIRECTORY.rglob("*.py")))


def run_example(path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=15,
    )


@pytest.mark.parametrize(
    "example",
    RUNNABLE_EXAMPLES,
    ids=lambda path: str(path.relative_to(EXAMPLE_DIRECTORY)),
)
def test_every_python_example_runs_and_prints_json(example: Path) -> None:
    result = run_example(example)

    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_example_index_links_every_runnable_example() -> None:
    index = (EXAMPLE_DIRECTORY / "README.md").read_text(encoding="utf-8")

    for example in RUNNABLE_EXAMPLES:
        relative = example.relative_to(EXAMPLE_DIRECTORY).as_posix()
        assert f"({relative})" in index


def test_mcp_stdio_client_example():
    result = run_example(EXAMPLE_DIRECTORY / "integrations" / "mcp_stdio_client.py")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["server"] == "sharkrail"
    assert "sharkrail_run" in payload["tools"]
    assert payload["platform"]
