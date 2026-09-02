import subprocess
import sys
import json
import os


def test_sharkrail_run_dry_run():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "echo", "hello", "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0


def test_sharkrail_no_subcommand_prints_help():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    assert "usage" in result.stdout.lower() or "usage" in result.stderr.lower()


def test_sharkrail_run_json_output():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "echo", "hello", "--dry-run", "--json"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout.strip())
    assert payload["timed_out"] is False
    assert payload["exit_code"] == 0
