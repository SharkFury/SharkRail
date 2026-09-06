"""Read lifecycle events by cursor and inspect bounded runtime telemetry."""

from __future__ import annotations

import asyncio
import json
import sys

from sharkrail import CommandSpec, SessionManager


async def main() -> None:
    manager = SessionManager(max_event_page_size=10)
    session = await manager.start(
        CommandSpec(sys.executable, ("-c", "print('observable')")),
        timeout_ms=5_000,
        trace_id="example-trace",
        request_id="agent-call-42",
    )
    try:
        result = await manager.wait(session.id)
        assert result is not None
        events, next_cursor, has_more = await manager.event_page(session.id, limit=10)
        inspection = manager.inspect(session.id)
        print(
            json.dumps(
                {
                    "event_kinds": [event.kind.value for event in events],
                    "next_cursor": next_cursor,
                    "has_more": has_more,
                    "trace_id": inspection["trace_id"],
                    "request_id": inspection["request_id"],
                    "stats": manager.stats()["sessions"],
                }
            )
        )
    finally:
        await manager.dispose(session.id)


if __name__ == "__main__":
    asyncio.run(main())
