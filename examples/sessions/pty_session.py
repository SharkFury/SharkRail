"""Use a real PTY/ConPTY for an interactive terminal session."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandMode, SessionManager, collect, direct_command


async def main() -> None:
    if "pty" not in collect().modes:
        print(json.dumps({"skipped": "PTY/ConPTY is unavailable"}))
        return
    manager = SessionManager()
    session = await manager.start(
        direct_command(
            sys.executable,
            ("-c", "import sys; print(input().upper(), flush=True)"),
            mode=CommandMode.PTY,
        ),
        timeout_ms=5_000,
        output_retention="tail",
    )
    try:
        await manager.resize(session.id, 100, 30)
        await manager.write(session.id, b"terminal input\n")
        result = await manager.wait(session.id)
        assert result is not None
        print(json.dumps({"mode": "pty", "output": result.stdout.strip()}))
    finally:
        await manager.dispose(session.id)


if __name__ == "__main__":
    asyncio.run(main())
