"""Compare total/idle deadlines with an explicit cancellation request."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CancellationPolicy, CommandRunner, CommandSpec, SessionManager


async def main() -> None:
    sleeper = CommandSpec(sys.executable, ("-c", "import time; time.sleep(30)"))
    timed_out = await CommandRunner().run(sleeper, timeout_ms=100)
    idle_timed_out = await CommandRunner().run(sleeper, idle_timeout_ms=100)

    manager = SessionManager()
    session = await manager.start(sleeper)
    try:
        await asyncio.sleep(0.05)
        steps = await manager.cancel(
            session.id,
            CancellationPolicy(
                interrupt_grace_ms=50,
                terminate_grace_ms=50,
                kill_tree_grace_ms=1_000,
            ),
        )
        cancelled = await manager.wait(session.id)
        assert cancelled is not None
        print(
            json.dumps(
                {
                    "deadline_reason": timed_out.reason.value,
                    "idle_deadline_reason": idle_timed_out.reason.value,
                    "cancel_reason": cancelled.reason.value,
                    "cancel_steps": list(steps),
                }
            )
        )
    finally:
        await manager.dispose(session.id)


if __name__ == "__main__":
    asyncio.run(main())
