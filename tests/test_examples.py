import json
import os
import subprocess
import sys


def test_mcp_stdio_client_example():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "examples/mcp_stdio_client.py"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["server"] == "sharkrail"
    assert "sharkrail_run" in payload["tools"]
    assert payload["platform"]
