"""Expose a SharkRail PTY through ACP v1 client-side terminal methods."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import AcpTerminalAdapter, collect


async def main() -> None:
    if "pty" not in collect().modes:
        print(json.dumps({"skipped": "PTY/ConPTY is unavailable"}))
        return
    adapter = AcpTerminalAdapter()
    owner = "acp-session-example"
    created = await adapter.handle(
        "terminal/create",
        {
            "sessionId": owner,
            "command": sys.executable,
            "args": ["-c", "print('acp-ok')"],
            "outputByteLimit": 4_096,
        },
    )
    terminal_id = created["terminalId"]
    try:
        exit_status = await adapter.handle(
            "terminal/wait_for_exit",
            {"sessionId": owner, "terminalId": terminal_id},
        )
        output = await adapter.handle(
            "terminal/output",
            {"sessionId": owner, "terminalId": terminal_id},
        )
        print(json.dumps({"output": output["output"].strip(), "exit": exit_status}))
    finally:
        await adapter.handle(
            "terminal/release",
            {"sessionId": owner, "terminalId": terminal_id},
        )


if __name__ == "__main__":
    asyncio.run(main())
