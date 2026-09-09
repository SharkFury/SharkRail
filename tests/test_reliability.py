import asyncio
import random
import sys
from types import SimpleNamespace

import pytest

from sharkrail.core.errors import ErrorCode, SharkRailError
from sharkrail.core.models import CommandSpec
from sharkrail.integrations.protocol import JsonRpcRuntime
from sharkrail.runtime.backends import ExecutionBackend, PipeBackend, ProcessHandle
from sharkrail.runtime.executor import LifecycleEventType
from sharkrail.runtime.sessions import Session, SessionManager


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


class _StartedButBlockedBackend(ExecutionBackend):
    def __init__(self) -> None:
        self.created = asyncio.Event()
        self.release = asyncio.Event()
        self.killed = False
        self.disposed = False
        self.start_cancelled = False

        async def wait() -> int:
            while self.process.returncode is None:
                await asyncio.sleep(0)
            return self.process.returncode

        self.process = SimpleNamespace(
            pid=123,
            returncode=None,
            stdout=None,
            stderr=None,
            stdin=None,
            wait=wait,
        )

    async def start(self, spec: CommandSpec) -> ProcessHandle:
        handle = ProcessHandle(process=self.process, process_tree="process_group")
        self.created.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            # A backend whose start has created a process must cooperate with
            # cancellation when it cannot return the handle before the
            # manager's bounded settle deadline.
            self.start_cancelled = True
            self.killed = True
            self.disposed = True
            self.process.returncode = -9
            raise
        return handle

    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        pass

    async def close_stdin(self, handle: ProcessHandle) -> None:
        pass

    async def interrupt(self, handle: ProcessHandle) -> None:
        pass

    async def terminate(self, handle: ProcessHandle) -> None:
        pass

    async def kill_tree(self, handle: ProcessHandle) -> None:
        self.killed = True
        self.process.returncode = -9

    async def dispose(self, handle: ProcessHandle) -> None:
        self.disposed = True


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


def test_cancelled_start_reaps_process_created_before_backend_returns():
    async def _run() -> None:
        backend = _StartedButBlockedBackend()
        manager = SessionManager(backend=backend, termination_timeout_ms=100)
        starting = asyncio.create_task(
            manager.start(CommandSpec(executable="fake", argv=()))
        )
        await backend.created.wait()

        starting.cancel()
        backend.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(starting, timeout=1)

        assert backend.killed is True
        assert backend.disposed is True
        assert manager.session_count == 0
        assert manager.stats()["sessions"]["starting"] == 0

    asyncio.run(_run())


def test_cancelled_start_has_a_hard_settle_deadline():
    async def _run() -> None:
        backend = _StartedButBlockedBackend()
        manager = SessionManager(
            backend=backend,
            termination_timeout_ms=20,
            shutdown_timeout_ms=50,
        )
        starting = asyncio.create_task(
            manager.start(CommandSpec(executable="fake", argv=()))
        )
        await backend.created.wait()

        started = asyncio.get_running_loop().time()
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(starting, timeout=0.5)

        assert asyncio.get_running_loop().time() - started < 0.25
        assert backend.start_cancelled is True
        assert backend.killed is True
        assert backend.disposed is True
        assert manager.session_count == 0
        assert manager.stats()["sessions"]["starting"] == 0

    asyncio.run(_run())


def test_cancelled_start_reaps_process_during_session_registration(monkeypatch):
    async def _run() -> None:
        backend = _StartedButBlockedBackend()
        backend.release.set()
        manager = SessionManager(backend=backend, termination_timeout_ms=100)
        entered_emit = asyncio.Event()

        async def stalled_emit(self, kind, payload=None):
            if kind == LifecycleEventType.ACCEPTED:
                entered_emit.set()
                await asyncio.Event().wait()

        monkeypatch.setattr(Session, "emit", stalled_emit)
        starting = asyncio.create_task(
            manager.start(CommandSpec(executable="fake", argv=()))
        )
        await entered_emit.wait()

        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(starting, timeout=1)

        assert backend.killed is True
        assert backend.disposed is True
        assert manager.session_count == 0
        assert manager.stats()["sessions"]["starting"] == 0
        assert manager.stats()["sessions"]["started"] == 0

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


def test_process_ownership_is_registered_before_start_returns_and_unregistered_last():
    class Ownership:
        def __init__(self):
            self.events = []

        def register(self, handle):
            assert handle.birth_identity is not None
            handle._ownership_id = "owned"
            self.events.append(("register", handle._disposed))

        def unregister(self, handle):
            self.events.append(("unregister", handle._disposed))
            handle._ownership_id = None

    async def _run() -> None:
        ownership = Ownership()
        manager = SessionManager(process_ownership=ownership)
        session = await manager.start(
            CommandSpec(executable=sys.executable, argv=("-c", "pass"))
        )
        assert ownership.events == [("register", False)]

        await manager.wait(session.id)

        assert ownership.events == [("register", False), ("unregister", True)]
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
