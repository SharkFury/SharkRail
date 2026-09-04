"""Smoke checks intended to run against an installed SharkRail wheel."""

from __future__ import annotations

import importlib.resources
import json
import subprocess
import sys

import sharkrail


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sharkrail", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


assert sharkrail.__version__
assert importlib.resources.files("sharkrail").joinpath("py.typed").is_file()
assert sharkrail.protocol_schema()["$id"].startswith("https://")

version = run("--version")
assert version.returncode == 0, version.stderr

capabilities = run("capabilities", "--json")
assert capabilities.returncode == 0, capabilities.stderr
payload = json.loads(capabilities.stdout)
assert "pipe" in payload["modes"]

command = run("run", "--json", "--", sys.executable, "-c", "print('wheel-ok')")
assert command.returncode == 0, command.stderr
assert json.loads(command.stdout)["stdout"].splitlines() == ["wheel-ok"]

print("installed wheel smoke passed")
