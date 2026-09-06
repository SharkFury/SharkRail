"""Drive a persistent process through the complete session lifecycle."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandSpec, SessionManager


async def main() -> None:
    manager = SessionManager()
    session = await manager.start(
        CommandSpec(sys.executable, ("-c", "print(input().upper())")),
        timeout_ms=5_000,
    )
    try:
        await manager.write(session.id, b"hello agent\n")
        await manager.close_stdin(session.id)
        result = await manager.wait(session.id)
        assert result is not None
        print(
            json.dumps({"state": session.state.value, "output": result.stdout.strip()})
        )
    finally:
        await manager.dispose(session.id)


if __name__ == "__main__":
    asyncio.run(main())
