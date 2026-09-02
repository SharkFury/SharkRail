import json
import os
import subprocess
import sys


def test_sharkrail_run_dry_run():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "echo", "hello", "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
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
        check=False,
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
        check=False,
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
        check=False,
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
        check=False,
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
        check=False,
        env=env,
    )
    payload = json.loads(result.stdout.strip())
    assert result.returncode == 0
    assert "platform" in payload
    assert "modes" in payload and isinstance(payload["modes"], list)
    assert "contract_version" in payload
    assert "features" in payload and isinstance(payload["features"], list)
    assert payload["supports_timeout"] is True


def test_sharkrail_run_json_with_events():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "--json", "--dry-run", "--events", "echo", "hello"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    payload = json.loads(result.stdout.strip())
    assert result.returncode == 0
    assert "events" in payload
    assert payload["events"][0]["kind"] == "accepted"


def test_sharkrail_run_json_nonzero_exit_is_failed():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "run", "--json", "--", sys.executable, "-c", "import sys; sys.exit(5)"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    payload = json.loads(result.stdout.strip())
    assert result.returncode == 5
    assert payload["reason"] == "failed"
    assert payload["exit_code"] == 5


def test_sharkrail_run_json_with_max_output_bytes_truncation():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sharkrail",
            "run",
            "--json",
            "--max-output-bytes",
            "6",
            "--",
            sys.executable,
            "-c",
            "print('0123456789')",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    payload = json.loads(result.stdout.strip())
    assert result.returncode == 0
    assert payload["output_truncated"] is True
    assert len(payload["stdout"]) <= 6


def test_sharkrail_serve_stdio_json_rpc():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    message = json.dumps(
        {"jsonrpc": "2.0", "id": 7, "method": "runtime.hello", "params": {}}
    )
    result = subprocess.run(
        [sys.executable, "-m", "sharkrail", "serve"],
        input=message + "\n",
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    payload = json.loads(result.stdout.strip())
    assert result.returncode == 0
    assert payload["id"] == 7
    assert payload["result"]["runtime"] == "SharkRail"
