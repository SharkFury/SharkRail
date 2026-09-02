import asyncio
import os
import sys

import pytest

from sharkrail.errors import ErrorCode, SharkRailError
from sharkrail.executor import CompletionReason, LifecycleEventType
from sharkrail.models import CommandMode, CommandSpec
from sharkrail.sessions import SessionManager, SessionState


def test_session_streams_input_output_and_events():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import sys; print(sys.stdin.readline().strip().upper())"),
            )
        )
        await manager.write(session.id, b"hello\n")
        await manager.close_stdin(session.id)
        result = await manager.wait(session.id)

        assert result is not None
        assert result.stdout == "HELLO\n"
        assert session.state == SessionState.COMPLETED
        assert [event.seq for event in session.events] == list(range(len(session.events)))
        assert session.events[-1].kind == LifecycleEventType.SESSION_COMPLETED
        assert any(event.kind == LifecycleEventType.STDOUT for event in session.events)

    asyncio.run(_run())


def test_session_event_cursor_and_bounded_output():
    async def _run() -> None:
        manager = SessionManager(default_max_output_bytes=4)
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "print('abcdefgh')"))
        )
        result = await manager.wait(session.id)
        events = await manager.events_after(session.id, cursor=2)

        assert result is not None
        assert result.output_truncated is True
        assert result.retained_output_bytes == 4
        assert any(event.kind == LifecycleEventType.OUTPUT_TRUNCATED for event in events)

    asyncio.run(_run())


def test_session_timeout_and_non_destructive_wait_timeout():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)")),
            timeout_ms=100,
        )
        assert await manager.wait(session.id, timeout_ms=1) is None
        result = await manager.wait(session.id)
        assert result is not None
        assert result.reason == CompletionReason.TIMEOUT
        assert result.exit_code == 124

    asyncio.run(_run())


def test_session_missing_id_is_structured_error():
    async def _run() -> None:
        manager = SessionManager()
        with pytest.raises(SharkRailError) as raised:
            manager.get("missing")
        assert raised.value.error.code == ErrorCode.SESSION_NOT_FOUND

    asyncio.run(_run())


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY backend")
def test_persistent_pty_session_supports_resize_and_write():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "print(input().upper())"),
                mode=CommandMode.PTY,
            )
        )
        await manager.resize(session.id, 120, 50)
        await manager.write(session.id, b"sharkrail\n")
        result = await manager.wait(session.id)
        assert result is not None
        assert "SHARKRAIL" in result.stdout

    asyncio.run(_run())
