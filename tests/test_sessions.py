import asyncio
import sys
from unittest.mock import patch

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


def test_monitor_failure_reaches_failed_terminal_state():
    async def _run() -> None:
        manager = SessionManager()
        with patch(
            "sharkrail.sessions.Session.append_output",
            side_effect=OSError("simulated output failure"),
        ):
            session = await manager.start(
                CommandSpec(executable=sys.executable, argv=("-c", "print('output')"))
            )
            result = await manager.wait(session.id)

        assert result is not None
        assert result.reason == CompletionReason.FAILED
        assert result.error is not None
        assert result.error.stage.value == "drain"
        assert session.state == SessionState.FAILED
        assert session.events[-2].kind == LifecycleEventType.SESSION_ERROR
        assert session.events[-1].kind == LifecycleEventType.SESSION_COMPLETED
        await manager.dispose(session.id)

    asyncio.run(_run())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX inherited pipe fixture")
def test_drain_timeout_kills_descendants_holding_output_open():
    async def _run() -> None:
        manager = SessionManager(drain_timeout_ms=50)
        code = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); "
            "print('parent exited')"
        )
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", code))
        )
        result = await asyncio.wait_for(manager.wait(session.id), timeout=2)

        assert result is not None
        assert result.reason == CompletionReason.RESOURCE_LIMITED
        assert result.error is not None
        assert result.error.code == ErrorCode.DRAIN_TIMEOUT
        assert session.state == SessionState.FAILED
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_cancel_and_dispose_are_idempotent():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)"))
        )
        first, second = await asyncio.gather(
            manager.cancel(session.id),
            manager.cancel(session.id),
        )

        assert first
        assert second == first
        assert len(session.cancellation_steps) == len(set(session.cancellation_steps))
        await manager.wait(session.id)
        await manager.dispose(session.id)
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_event_history_is_bounded_and_reports_expired_cursor():
    async def _run() -> None:
        manager = SessionManager(max_retained_events=4, max_output_events=20)
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(.2)"))
        )
        for index in range(8):
            await session.emit(LifecycleEventType.CANCELLATION_STEP, {"index": index})

        assert len(session.events) == 4
        with pytest.raises(SharkRailError) as raised:
            await manager.events_after(session.id, cursor=0)
        assert raised.value.error.code == ErrorCode.EVENT_CURSOR_EXPIRED
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_event_pages_have_absolute_cursors():
    async def _run() -> None:
        manager = SessionManager(max_event_page_size=2)
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "print('page')"))
        )
        await manager.wait(session.id)
        events, next_cursor, has_more = await manager.event_page(
            session.id,
            cursor=session.first_event_seq,
            limit=2,
        )

        assert len(events) == 2
        assert next_cursor == events[-1].seq + 1
        assert has_more is True
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_completed_sessions_expire_with_structured_error():
    async def _run() -> None:
        manager = SessionManager(completed_session_ttl_ms=0)
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "pass"))
        )
        await manager.wait(session.id)

        with pytest.raises(SharkRailError) as raised:
            manager.get(session.id)
        assert raised.value.error.code == ErrorCode.SESSION_EXPIRED

    asyncio.run(_run())


def test_runtime_stats_and_session_inspection_are_bounded_metadata():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "print('metrics')")),
            trace_id="trace-test",
            request_id="request-test",
        )
        result = await manager.wait(session.id)
        stats = manager.stats()
        inspected = manager.inspect(session.id)

        assert result is not None and result.duration_ms >= 0
        assert stats["sessions"]["started"] == 1
        assert stats["sessions"]["completed_by_reason"]["success"] == 1
        assert stats["io"]["output_bytes"] >= len("metrics\n")
        assert inspected["trace_id"] == "trace-test"
        assert inspected["request_id"] == "request-test"
        assert "argv" not in inspected
        assert "env" not in inspected
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_session_idle_timeout_is_distinct_from_wall_timeout():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(5)")),
            idle_timeout_ms=50,
        )
        result = await asyncio.wait_for(manager.wait(session.id), timeout=2)

        assert result is not None
        assert result.reason == CompletionReason.IDLE_TIMEOUT
        assert result.timed_out is True
        assert result.exit_code == 124
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_session_enforces_total_input_limit():
    async def _run() -> None:
        manager = SessionManager(max_input_bytes=4, max_total_input_bytes=5)
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import sys; sys.stdin.read()"))
        )
        await manager.write(session.id, b"123")
        with pytest.raises(SharkRailError) as raised:
            await manager.write(session.id, b"456")

        assert raised.value.error.code == ErrorCode.RESOURCE_LIMITED
        assert any(
            event.kind == LifecycleEventType.RESOURCE_LIMIT_HIT
            and event.payload.get("resource") == "total_input_bytes"
            for event in session.events
        )
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_streaming_utf8_decoder_handles_character_split_between_chunks():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "import time; time.sleep(.1)"))
        )
        await session.append_output("stdout", b"\xe4")
        await session.append_output("stdout", b"\xb8\xad")
        result = await manager.wait(session.id)
        output_events = [
            event for event in session.events if event.kind == LifecycleEventType.STDOUT
        ]

        assert result is not None and result.stdout == "中"
        assert "".join(str(event.payload["text"]) for event in output_events) == "中"
        assert not any(event.payload["decoding_errors"] for event in output_events)
        await manager.dispose(session.id)

    asyncio.run(_run())
