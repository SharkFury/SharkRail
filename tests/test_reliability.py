import asyncio
import random
import sys
from types import SimpleNamespace

import pytest

from sharkrail.backends import ExecutionBackend, PipeBackend, ProcessHandle
from sharkrail.errors import ErrorCode, SharkRailError
from sharkrail.executor import LifecycleEventType
from sharkrail.models import CommandSpec
from sharkrail.protocol import JsonRpcRuntime
from sharkrail.sessions import SessionManager


class _SlowStartBackend(ExecutionBackend):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self, spec: CommandSpec) -> ProcessHandle:
        self.entered.set()
        await self.release.wait()

        async def wait() -> int:
            return 0

        process = SimpleNamespace(
            pid=123,
            returncode=0,
            stdout=None,
            stderr=None,
            wait=wait,
        )
        return ProcessHandle(process=process)

    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        pass

    async def close_stdin(self, handle: ProcessHandle) -> None:
        pass

    async def interrupt(self, handle: ProcessHandle) -> None:
        pass

    async def terminate(self, handle: ProcessHandle) -> None:
        pass

    async def kill_tree(self, handle: ProcessHandle) -> None:
        pass

    async def dispose(self, handle: ProcessHandle) -> None:
        pass


def test_concurrent_session_admission_is_atomic():
    async def _run() -> None:
        backend = _SlowStartBackend()
        manager = SessionManager(max_active_sessions=1, backend=backend)
        spec = CommandSpec(executable="fake", argv=())
        first = asyncio.create_task(manager.start(spec))
        await backend.entered.wait()

        with pytest.raises(SharkRailError) as raised:
            await manager.start(spec)
        assert raised.value.error.code == ErrorCode.RESOURCE_LIMITED

        backend.release.set()
        session = await first
        await manager.wait(session.id)
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_failed_start_releases_admission_reservation():
    async def _run() -> None:
        manager = SessionManager(max_active_sessions=1)
        with pytest.raises(SharkRailError):
            await manager.start(CommandSpec(executable="__missing__", argv=()))

        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "pass"))
        )
        assert manager.stats()["sessions"]["active"] == 1
        await manager.wait(session.id)
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_dispose_releases_backend_after_cancellation_error():
    class FailingInterruptBackend(PipeBackend):
        disposed = False

        async def interrupt(self, handle: ProcessHandle) -> None:
            raise RuntimeError("injected interrupt failure")

        async def dispose(self, handle: ProcessHandle) -> None:
            self.disposed = True
            await super().dispose(handle)

    async def _run() -> None:
        backend = FailingInterruptBackend()
        manager = SessionManager(backend=backend, termination_timeout_ms=1000)
        session = await manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import time; time.sleep(10)"),
            )
        )

        with pytest.raises(SharkRailError) as raised:
            await manager.cancel(session.id)
        assert raised.value.error.code == ErrorCode.TERMINATION_FAILED
        assert raised.value.error.native["cleanup_succeeded"] is True

        await manager.dispose(session.id)
        assert backend.disposed is True
        assert manager.session_count == 0

    asyncio.run(_run())


def test_large_output_keeps_memory_and_event_history_bounded():
    async def _run() -> None:
        manager = SessionManager(
            default_max_output_bytes=1024,
            max_output_events=4,
            max_retained_events=8,
        )
        session = await manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=("-c", "import sys; sys.stdout.write('x' * 5000000)"),
            )
        )
        result = await manager.wait(session.id)

        assert result is not None
        assert result.retained_output_bytes == 1024
        assert result.truncated_output_bytes == 5_000_000 - 1024
        assert len(session.stdout) + len(session.stderr) == 1024
        assert len(session.events) <= 8
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_long_poll_uses_predicate_and_does_not_lose_wakeup():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(
                executable=sys.executable, argv=("-c", "import time; time.sleep(.2)")
            )
        )
        cursor = session.next_event_seq
        subscriber = asyncio.create_task(
            manager.event_page(session.id, cursor=cursor, wait_ms=1000)
        )
        await asyncio.sleep(0)
        await session.emit(LifecycleEventType.CANCELLATION_STEP, {"step": "probe"})
        events, _, _ = await asyncio.wait_for(subscriber, timeout=0.2)

        assert events[0].payload["step"] == "probe"
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_concurrent_writes_are_serialized_without_data_loss():
    async def _run() -> None:
        manager = SessionManager()
        session = await manager.start(
            CommandSpec(
                executable=sys.executable,
                argv=(
                    "-c",
                    "import sys; lines=[sys.stdin.readline().strip() for _ in range(20)]; print(','.join(sorted(lines)))",
                ),
            )
        )
        await asyncio.gather(
            *(
                manager.write(session.id, f"{index:02d}\n".encode())
                for index in range(20)
            )
        )
        await manager.close_stdin(session.id)
        result = await manager.wait(session.id)

        assert result is not None
        assert result.stdout.strip().split(",") == [
            f"{index:02d}" for index in range(20)
        ]
        await manager.dispose(session.id)

    asyncio.run(_run())


def test_protocol_boundary_survives_random_invalid_requests():
    async def _run() -> None:
        randomizer = random.Random(42)
        runtime = JsonRpcRuntime()
        values: list[object] = [None, True, 1, "request", [], {}, {"jsonrpc": "1.0"}]
        for _ in range(200):
            request = randomizer.choice(values)
            response = await runtime.dispatch(request)
            assert response is None or response.get("jsonrpc") == "2.0"

    asyncio.run(_run())
