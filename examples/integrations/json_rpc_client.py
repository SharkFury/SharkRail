"""Minimal host for the complete newline-delimited JSON-RPC interface."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, TextIO


def request(
    source: TextIO,
    destination: TextIO,
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    destination.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        + "\n"
    )
    destination.flush()
    response = json.loads(source.readline())
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


def main() -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "sharkrail", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        hello = request(process.stdout, process.stdin, 1, "runtime.hello", {})
        started = request(
            process.stdout,
            process.stdin,
            2,
            "session.start",
            {
                "spec": {
                    "executable": sys.executable,
                    "argv": ["-c", "print('rpc-ok')"],
                },
                "timeout_ms": 5_000,
            },
        )
        session_id = started["session_id"]
        result = request(
            process.stdout,
            process.stdin,
            3,
            "session.wait",
            {"session_id": session_id},
        )
        request(
            process.stdout,
            process.stdin,
            4,
            "session.dispose",
            {"session_id": session_id},
        )
        print(
            json.dumps(
                {
                    "protocol": hello["protocol_version"],
                    "reason": result["reason"],
                    "output": result["stdout"].strip(),
                }
            )
        )
    finally:
        process.stdin.close()
        process.wait(timeout=5)


if __name__ == "__main__":
    main()
