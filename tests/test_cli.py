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
    assert "capabilities" in result.stdout.lower() or "capabilities" in result.stderr.lower()


def test_sharkrail_run_json_output():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "--json", "--dry-run", "echo", "hello"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout.strip())
    assert payload["reason"] == "success"
    assert payload["timed_out"] is False
    assert payload["exit_code"] == 0


def test_sharkrail_run_timeout_json_exit_code():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "--json", "--timeout-ms", "200", "python3", "--", "-c", "import time; time.sleep(10)"],
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout.strip())
    assert result.returncode == 124
    assert payload["reason"] == "timeout"
    assert payload["timed_out"] is True


def test_sharkrail_version():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "--version"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("sharkrail ")


def test_sharkrail_capabilities_json():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "capabilities", "--json"],
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout.strip())
    assert result.returncode == 0
    assert "platform" in payload
    assert "modes" in payload and isinstance(payload["modes"], list)
    assert payload["supports_timeout"] is True
