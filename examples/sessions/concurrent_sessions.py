"""Run several independent agent commands through one bounded manager."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandSpec, SessionManager


async def main() -> None:
    manager = SessionManager(max_active_sessions=3)
    sessions = [
        await manager.start(
            CommandSpec(sys.executable, ("-c", f"print('worker-{index}')")),
            timeout_ms=5_000,
        )
        for index in range(3)
    ]
    try:
        results = await asyncio.gather(
            *(manager.wait(session.id) for session in sessions)
        )
        print(
            json.dumps(
                {
                    "outputs": [result.stdout.strip() for result in results if result],
                    "started": manager.stats()["sessions"]["started"],
                }
            )
        )
    finally:
        await manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
