import asyncio
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
        assert result.stdout.splitlines() == ["HELLO"]
        assert session.state == SessionState.COMPLETED
        assert session.lifecycle.history == [
            SessionState.CREATED,
            SessionState.ACCEPTED,
            SessionState.STARTING,
            SessionState.RUNNING,
            SessionState.EXITING,
            SessionState.DRAINING,
            SessionState.COMPLETED,
        ]
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
        await manager.write(session.id, b"sharkrail\r\n")
        result = await asyncio.wait_for(manager.wait(session.id), timeout=10)
        assert result is not None
        assert "SHARKRAIL" in result.stdout

    asyncio.run(_run())


def test_session_manager_enforces_concurrency_limit():
    async def _run() -> None:
        manager = SessionManager(max_active_sessions=1)
        first = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)"))
        )
        with pytest.raises(SharkRailError) as raised:
            await manager.start(CommandSpec(executable=sys.executable, argv=("-c", "pass")))
        assert raised.value.error.code == ErrorCode.RESOURCE_LIMITED
        assert raised.value.error.retryable is True
        await manager.dispose(first.id)

    asyncio.run(_run())


def test_session_manager_enforces_input_limit():
    async def _run() -> None:
        manager = SessionManager(max_input_bytes=4)
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import sys; sys.stdin.read()"))
        )
        with pytest.raises(SharkRailError) as raised:
            await manager.write(session.id, b"12345")
        assert raised.value.error.code == ErrorCode.RESOURCE_LIMITED
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_session_manager_limits_output_event_count():
    async def _run() -> None:
        manager = SessionManager(max_output_events=1)
        session = await manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import sys; sys.stdout.write('a'*200000); sys.stdout.flush()"),
            )
        )
        await manager.wait(session.id)
        output_events = [event for event in session.events if event.kind == LifecycleEventType.STDOUT]
        assert len(output_events) == 1
        assert any(event.kind == LifecycleEventType.RESOURCE_LIMIT_HIT for event in session.events)

    asyncio.run(_run())


def test_session_manager_shutdown_disposes_all_sessions():
    async def _run() -> None:
        manager = SessionManager()
        await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)"))
        )
        await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)"))
        )
        await manager.shutdown()
        assert manager.session_count == 0

    asyncio.run(_run())
