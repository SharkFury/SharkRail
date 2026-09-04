"""Minimal synchronous MCP host that discovers and calls SharkRail tools."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, TextIO

from sharkrail import MCP_PROTOCOL_VERSION


def _send(stream: TextIO, message: dict[str, Any]) -> None:
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


def _request(
    source: TextIO,
    destination: TextIO,
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    _send(
        destination,
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
    )
    response = json.loads(source.readline())
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


def main() -> int:
    process = subprocess.Popen(
        [sys.executable, "-m", "sharkrail", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        initialized = _request(
            process.stdout,
            process.stdin,
            1,
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "sharkrail-example", "version": "1"},
            },
        )
        _send(
            process.stdin,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        tools = _request(process.stdout, process.stdin, 2, "tools/list", {})
        capabilities = _request(
            process.stdout,
            process.stdin,
            3,
            "tools/call",
            {"name": "sharkrail_capabilities", "arguments": {}},
        )
        print(
            json.dumps(
                {
                    "server": initialized["serverInfo"]["name"],
                    "tools": [tool["name"] for tool in tools["tools"]],
                    "platform": capabilities["structuredContent"]["platform"],
                }
            )
        )
    finally:
        process.stdin.close()
        process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
