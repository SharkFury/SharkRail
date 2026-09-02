import subprocess
import sys


def test_sharkrail_run_dry_run():
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "echo", "hello", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_sharkrail_no_subcommand_prints_help():
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "usage" in result.stdout.lower() or "usage" in result.stderr.lower()

