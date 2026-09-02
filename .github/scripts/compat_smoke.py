from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"compat smoke failed: {message}")


ROOT = Path(__file__).resolve().parents[2]
env = os.environ.copy()
env["PYTHONPATH"] = str(ROOT / "src")


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, env=env)


version = run([sys.executable, "-m", "sharkrail", "--version"])
expect(version.returncode == 0, "--version should return 0")

dry = run([sys.executable, "-m", "sharkrail", "run", "--dry-run", "--json", "echo", "hello"])
expect(dry.returncode == 0, "dry-run should return 0")
json_payload = json.loads(dry.stdout)
expect(json_payload["reason"] == "success", "dry-run json should include reason=success")

no_cmd = run([sys.executable, "-m", "sharkrail"])
expect(no_cmd.returncode != 0, "missing subcommand should fail")

run_json = run([sys.executable, "-m", "sharkrail", "run", "--json", "--dry-run", "echo", "hello"])
expect(run_json.returncode == 0, "json dry-run should return 0")
run_payload = json.loads(run_json.stdout)
expect(run_payload["reason"] == "success", "run --json should include reason=success")
expect(run_payload["timed_out"] is False, "run --json should not report timeout")

timeout = run(
    [
        sys.executable,
        "-m",
        "sharkrail",
        "run",
        "--json",
        "--timeout-ms",
        "200",
        "python3",
        "--",
        "-c",
        "import time; time.sleep(10)",
    ]
)
expect(timeout.returncode == 124, "timeout should return 124")
timeout_payload = json.loads(timeout.stdout)
expect(timeout_payload["reason"] == "timeout", "timeout should report reason=timeout")
expect(timeout_payload["timed_out"] is True, "timeout should mark timed_out")

capabilities = run([sys.executable, "-m", "sharkrail", "capabilities", "--json"])
expect(capabilities.returncode == 0, "capabilities --json should return 0")
cap_payload = json.loads(capabilities.stdout)
expect("platform" in cap_payload, "capabilities payload should include platform")
expect("modes" in cap_payload, "capabilities payload should include modes")
expect(isinstance(cap_payload["modes"], list), "capabilities modes should be a list")
expect(cap_payload.get("supports_timeout") is True, "capabilities should report timeout support")

print("compat smoke passed")
