import asyncio
import os
import socket
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from sharkrail.core.errors import ErrorCode, SharkRailError
from sharkrail.core.models import CommandMode, CommandSpec, ResourceLimits
from sharkrail.runtime.backends import (
    PipeBackend,
    ProcessHandle,
    PtyBackend,
    WindowsPipeBackend,
    WindowsProcessHandle,
    WindowsPtyBackend,
    WindowsPtyProcessHandle,
    _child_environment,
    _conpty_broker_main,
    _raise_broker_error,
    _windows_terminal_input,
    _WinPtyAsyncProcess,
    pipe_backend,
    pty_backend,
)
from sharkrail.runtime.sessions import SessionManager
from sharkrail.runtime.windows import WindowsJob
from sharkrail.service import windows_security
from sharkrail.service.windows_security import (
    read_verified_text_file,
    secure_private_path,
    validate_private_file,
    validate_private_path,
)


class FakeProcess:
    pid = 123
    returncode = None
    stdin = None

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


def test_platform_pipe_backend_selection():
    backend = pipe_backend()
    if os.name == "nt":
        assert isinstance(backend, WindowsPipeBackend)
    else:
        assert type(backend) is PipeBackend


def test_clean_windows_environment_keeps_only_loader_bootstrap_and_overlay():
    with (
        patch.dict(
            "sharkrail.runtime.backends.os.environ",
            {"SYSTEMROOT": r"C:\Windows", "SECRET": "do-not-copy"},
            clear=True,
        ),
        patch("sharkrail.runtime.backends.os.name", "nt"),
    ):
        environment = _child_environment(
            CommandSpec("tool", (), env={"SAFE": "yes"}, inherit_env=False)
        )

    assert environment == {"SYSTEMROOT": r"C:\Windows", "SAFE": "yes"}


def test_windows_terminal_input_translates_lf_without_doubling_crlf():
    assert _windows_terminal_input(b"first\nsecond\r\n") == b"first\r\nsecond\r\n"


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ConPTY fixture")
def test_windows_conpty_missing_executable_keeps_start_error_classification():
    async def _run() -> None:
        manager = SessionManager()
        try:
            with pytest.raises(SharkRailError) as raised:
                await manager.start(
                    CommandSpec(
                        "sharkrail-definitely-missing.exe", (), mode=CommandMode.PTY
                    )
                )
            assert raised.value.error.code == ErrorCode.EXECUTABLE_NOT_FOUND
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.skipif(os.name == "nt", reason="non-Windows guard assertion")
def test_windows_job_has_explicit_platform_guard():
    with pytest.raises(OSError, match="only available on Windows"):
        WindowsJob()


@pytest.mark.skipif(os.name == "nt", reason="POSIX device path")
def test_private_file_validation_rejects_device():
    with pytest.raises(PermissionError, match="regular file"):
        validate_private_file(Path("/dev/null"))


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL integration")
def test_windows_private_directory_acl_is_inherited(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    secure_private_path(private, directory=True)
    child = private / "secret.txt"
    child.write_text("secret", encoding="utf-8")

    validate_private_path(private)
    validate_private_path(child)
    assert read_verified_text_file(child, forbidden_permissions=0o027) == "secret"


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL integration")
def test_windows_private_acl_rejects_everyone_allow_ace(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    windows_security._set_windows_dacl(secret, "D:P(A;;FR;;;WD)")

    with pytest.raises(PermissionError, match="grants access outside"):
        validate_private_path(secret)


def test_platform_pty_backend_selection():
    backend = pty_backend()
    if os.name == "nt":
        assert isinstance(backend, WindowsPtyBackend)
    else:
        assert type(backend) is PtyBackend


def test_windows_pipe_fails_closed_when_job_assignment_is_unavailable():
    async def _run() -> None:
        process = FakeProcess()
        job = Mock()
        job.assign.side_effect = OSError("nested Job assignment is unavailable")
        backend = WindowsPipeBackend()

        with (
            patch.object(
                PipeBackend,
                "start",
                new=AsyncMock(return_value=ProcessHandle(process=process)),
            ),
            patch.object(PipeBackend, "kill_tree", new=AsyncMock()),
            patch("sharkrail.runtime.backends.WindowsJob", return_value=job),
            pytest.raises(OSError, match="nested Job assignment"),
        ):
            await backend.start(CommandSpec("tool", ()))

        assert process.killed is True
        job.close.assert_called_once_with()

    asyncio.run(_run())


def test_windows_pipe_does_not_start_process_when_job_construction_fails():
    async def _run() -> None:
        backend = WindowsPipeBackend()

        with (
            patch.object(
                PipeBackend,
                "start",
                new=AsyncMock(),
            ) as start,
            patch(
                "sharkrail.runtime.backends.WindowsJob",
                side_effect=OSError("Job construction failed"),
            ),
            pytest.raises(OSError, match="Job construction failed"),
        ):
            await backend.start(CommandSpec("tool", ()))

        start.assert_not_awaited()

    asyncio.run(_run())


def test_windows_pipe_requires_job_when_resource_limits_are_requested():
    async def _run() -> None:
        process = FakeProcess()
        job = Mock()
        job.assign.side_effect = OSError("nested Job assignment is unavailable")
        backend = WindowsPipeBackend()
        spec = CommandSpec(
            "tool",
            (),
            resources=ResourceLimits(memory_bytes=1024),
        )

        with (
            patch.object(
                PipeBackend,
                "start",
                new=AsyncMock(return_value=ProcessHandle(process=process)),
            ),
            patch.object(PipeBackend, "kill_tree", new=AsyncMock()),
            patch("sharkrail.runtime.backends.WindowsJob", return_value=job),
            pytest.raises(OSError, match="nested Job assignment"),
        ):
            await backend.start(spec)

        assert process.killed is True
        job.close.assert_called_once_with()

    asyncio.run(_run())


def test_windows_pipe_is_created_suspended_and_resumed_only_after_assignment():
    async def _run() -> None:
        process = FakeProcess()
        job = Mock()
        backend = WindowsPipeBackend()

        with (
            patch.object(
                PipeBackend,
                "start",
                new=AsyncMock(return_value=ProcessHandle(process=process)),
            ),
            patch("sharkrail.runtime.backends.WindowsJob", return_value=job),
        ):
            handle = await backend.start(CommandSpec("tool", ()))

        assert isinstance(handle, WindowsProcessHandle)
        assert handle.process_tree == "job_object"
        assert job.method_calls[:2] == [
            call.assign(123),
            call.resume(123),
        ]

    asyncio.run(_run())


def test_windows_pipe_creation_flags_include_suspended():
    flags = WindowsPipeBackend()._windows_creation_flags()

    assert flags & 0x00000004
    assert flags & 0x00000200


def test_windows_pipe_resume_failure_terminates_owned_job():
    async def _run() -> None:
        process = FakeProcess()
        job = Mock()
        job.resume.side_effect = OSError("ResumeThread failed")
        backend = WindowsPipeBackend()

        with (
            patch.object(
                PipeBackend,
                "start",
                new=AsyncMock(return_value=ProcessHandle(process=process)),
            ),
            patch("sharkrail.runtime.backends.WindowsJob", return_value=job),
            pytest.raises(OSError, match="ResumeThread"),
        ):
            await backend.start(CommandSpec("tool", ()))

        job.assign.assert_called_once_with(123)
        job.terminate.assert_called_once_with()
        job.wait_empty.assert_not_called()
        job.close.assert_called_once_with()
        assert process.killed is True

    asyncio.run(_run())


def test_windows_pipe_dispose_also_closes_standard_input():
    async def _run() -> None:
        process = FakeProcess()
        job = Mock()
        handle = WindowsProcessHandle(process=process, job=job)
        backend = WindowsPipeBackend()

        with patch.object(PipeBackend, "dispose", new=AsyncMock()) as dispose:
            await backend.dispose(handle)

        job.close.assert_called_once_with()
        dispose.assert_awaited_once_with(handle)

    asyncio.run(_run())


def test_windows_pipe_kill_waits_for_authoritative_empty_job_accounting():
    async def _run() -> None:
        job = Mock()
        handle = WindowsProcessHandle(process=FakeProcess(), job=job)

        await WindowsPipeBackend().kill_tree(handle)

        job.terminate.assert_called_once_with()
        job.wait_empty.assert_called_once_with(1.0)

    asyncio.run(_run())


def test_taskkill_fallback_does_not_target_an_exited_reusable_pid():
    async def _run() -> None:
        process = FakeProcess()
        process.returncode = 0
        handle = ProcessHandle(process=process, process_tree="taskkill_fallback")

        with (
            patch("sharkrail.runtime.backends.os.name", "nt"),
            patch(
                "sharkrail.runtime.backends.asyncio.create_subprocess_exec",
                new=AsyncMock(),
            ) as create_process,
        ):
            await PipeBackend().kill_tree(handle)

        create_process.assert_not_awaited()

    asyncio.run(_run())


def test_windows_pty_start_bounds_relay_reads():
    async def _run() -> None:
        native = Mock(pid=123)
        pty_process = Mock()
        pty_process.spawn.return_value = native
        winpty = ModuleType("winpty")
        winpty.PtyProcess = pty_process
        job = Mock()
        backend = WindowsPtyBackend()

        with (
            patch.dict(sys.modules, {"winpty": winpty}),
            patch("sharkrail.runtime.backends.os.name", "nt"),
            patch("sharkrail.runtime.backends.sys.platform", "test"),
            patch("sharkrail.runtime.backends.WindowsJob", return_value=job),
        ):
            handle = await backend.start(CommandSpec("tool", ()))

        assert isinstance(handle, WindowsPtyProcessHandle)
        assert handle.process_tree == "job_object"
        assert "pre-execution Job containment" in handle.degraded_reasons[0]
        native.fileobj.settimeout.assert_called_once_with(backend._read_poll_seconds)
        job.assign.assert_called_once_with(123)

    asyncio.run(_run())


def test_windows_pty_does_not_spawn_when_job_construction_fails():
    async def _run() -> None:
        native = Mock(pid=123)
        pty_process = Mock()
        pty_process.spawn.return_value = native
        winpty = ModuleType("winpty")
        winpty.PtyProcess = pty_process
        backend = WindowsPtyBackend()

        with (
            patch.dict(sys.modules, {"winpty": winpty}),
            patch("sharkrail.runtime.backends.os.name", "nt"),
            patch(
                "sharkrail.runtime.backends.WindowsJob",
                side_effect=OSError("Job construction failed"),
            ),
            pytest.raises(OSError, match="Job construction failed"),
        ):
            await backend.start(CommandSpec("tool", ()))

        pty_process.spawn.assert_not_called()

    asyncio.run(_run())


def test_windows_pty_broker_is_job_owned_before_user_spawn_is_released():
    async def _run() -> None:
        events = []
        control_parent = Mock()
        control_parent.poll.return_value = True
        control_parent.recv.return_value = ("ok", 123)
        control_parent.send.side_effect = lambda _message: events.append(
            "start-request"
        )
        output_parent = Mock()
        status_parent = Mock()
        child_connections = [Mock(), Mock(), Mock()]
        pipes = iter(
            (
                (control_parent, child_connections[0]),
                (output_parent, child_connections[1]),
                (status_parent, child_connections[2]),
            )
        )
        context = Mock()
        context.Pipe.side_effect = lambda **_kwargs: next(pipes)
        broker = Mock(pid=42, exitcode=None)
        context.Process.return_value = broker
        broker_job = Mock()
        broker_job.assign.side_effect = lambda _pid: events.append("job-assigned")
        user_job = Mock()
        user_job.duplicate_to_process.return_value = 84

        with (
            patch(
                "sharkrail.runtime.backends.multiprocessing.get_context",
                return_value=context,
            ),
            patch(
                "sharkrail.runtime.backends.WindowsJob",
                side_effect=(broker_job, user_job),
            ) as job_type,
        ):
            handle = await WindowsPtyBackend()._start_brokered(
                CommandSpec("tool", (), resources=ResourceLimits(process_count=3))
            )

        assert handle.pid == 123
        assert events == ["job-assigned", "start-request"]
        assert job_type.call_args_list == [
            call(),
            call(memory_bytes=None, cpu_time_seconds=None, process_count=3),
        ]
        broker_job.assign.assert_called_once_with(42)
        user_job.duplicate_to_process.assert_called_once_with(42)
        assert control_parent.send.call_args.args[0][-1] == 84
        assert handle.job is user_job
        assert handle.broker_job is broker_job

    asyncio.run(_run())


def test_conpty_broker_preserves_missing_executable_details():
    control = Mock()
    control.recv.return_value = (
        "start",
        ["missing.exe"],
        None,
        {},
        (24, 80),
        0.1,
        123,
    )
    output = Mock()
    status = Mock()
    winpty = ModuleType("winpty")
    winpty.PtyProcess = Mock()
    winpty.PtyProcess.spawn.side_effect = FileNotFoundError(2, "missing executable")
    user_job = Mock()

    with (
        patch.dict(sys.modules, {"winpty": winpty}),
        patch.object(WindowsJob, "from_handle", return_value=user_job),
    ):
        _conpty_broker_main(control, output, status)

    error = control.send.call_args_list[0].args[0]
    assert error[:3] == (
        "error",
        "FileNotFoundError",
        "[Errno 2] missing executable",
    )
    assert error[3] == 2
    with pytest.raises(FileNotFoundError) as raised:
        _raise_broker_error(error)
    assert raised.value.errno == 2
    user_job.close.assert_called_once_with()


def test_windows_pty_dispose_closes_jobs_before_stuck_broker_reaping():
    async def _run() -> None:
        entered = threading.Event()
        release = threading.Event()

        class Broker:
            exitcode = None

            def __init__(self):
                self.alive = True
                self.terminated = False
                self.closed = False

            def join(self, timeout):
                if not self.terminated:
                    entered.set()
                    release.wait(2)
                else:
                    self.alive = False

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.alive = False

            def close(self):
                self.closed = True

        broker = Broker()
        user_job = Mock()
        user_job.wait_empty.return_value = True
        broker_job = Mock()
        process = Mock(returncode=None, _status=Mock())
        handle = WindowsPtyProcessHandle(
            process=process,
            job=user_job,
            broker_job=broker_job,
            broker=broker,
            broker_control=Mock(),
            broker_output=Mock(),
        )

        disposing = asyncio.create_task(WindowsPtyBackend().dispose(handle))
        assert await asyncio.to_thread(entered.wait, 1)
        disposing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await disposing

        assert handle._disposed is True
        assert handle._tree_killed is True
        user_job.close.assert_called_once_with()
        broker_job.close.assert_called_once_with()
        release.set()
        deadline = time.monotonic() + 1
        while not broker.closed and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert broker.terminated is True
        assert broker.closed is True

    asyncio.run(_run())


def test_windows_pty_dispose_is_terminal_when_active_zero_check_fails():
    async def _run() -> None:
        user_job = Mock()
        user_job.wait_empty.return_value = False
        broker_job = Mock()
        native = Mock()
        native.isalive.return_value = False
        handle = WindowsPtyProcessHandle(
            process=Mock(returncode=None),
            native_pty=native,
            job=user_job,
            broker_job=broker_job,
        )

        with pytest.raises(TimeoutError, match="active processes"):
            await WindowsPtyBackend().dispose(handle)

        assert handle._disposed is True
        assert handle._tree_killed is True
        assert handle.job is None
        assert handle.broker_job is None
        user_job.close.assert_called_once_with()
        broker_job.close.assert_called_once_with()
        await WindowsPtyBackend().dispose(handle)

    asyncio.run(_run())


def test_cancelled_windows_pty_spawn_closes_late_native_process():
    async def _run() -> None:
        entered = threading.Event()
        release = threading.Event()
        native = Mock(pid=123)

        def blocked_spawn(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return native

        pty_process = Mock()
        pty_process.spawn.side_effect = blocked_spawn
        winpty = ModuleType("winpty")
        winpty.PtyProcess = pty_process
        job = Mock()
        backend = WindowsPtyBackend()

        with (
            patch.dict(sys.modules, {"winpty": winpty}),
            patch("sharkrail.runtime.backends.os.name", "nt"),
            patch("sharkrail.runtime.backends.sys.platform", "test"),
            patch("sharkrail.runtime.backends.WindowsJob", return_value=job),
        ):
            start = asyncio.create_task(backend.start(CommandSpec("tool", ())))
            assert await asyncio.to_thread(entered.wait, 1)
            start.cancel()
            with pytest.raises(asyncio.CancelledError):
                await start
            release.set()
            deadline = time.monotonic() + 1
            while not native.close.called and time.monotonic() < deadline:
                await asyncio.sleep(0.01)

        native.close.assert_called_once_with(force=True)
        job.close.assert_called_once_with()

    asyncio.run(_run())


def test_cancelled_windows_pty_write_rejects_overlapping_writes():
    async def _run() -> None:
        entered = threading.Event()
        release = threading.Event()

        def blocked_write(_data):
            entered.set()
            release.wait(2)

        native = Mock()
        native.write.side_effect = blocked_write
        handle = WindowsPtyProcessHandle(process=Mock(), native_pty=native)
        backend = WindowsPtyBackend()

        first = asyncio.create_task(backend.write(handle, b"first"))
        assert await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(RuntimeError, match="previous ConPTY write"):
            await backend.write(handle, b"second")
        release.set()
        deadline = time.monotonic() + 1
        while handle._write_future is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert handle._write_future is None

    asyncio.run(_run())


def test_windows_pty_rejects_new_writes_after_close_begins():
    async def _run() -> None:
        native = Mock()
        handle = WindowsPtyProcessHandle(process=Mock(), native_pty=native)
        handle._closing = True

        with pytest.raises(RuntimeError, match="stdin is closed"):
            await WindowsPtyBackend().write(handle, b"late")

        native.write.assert_not_called()

    asyncio.run(_run())


def test_windows_pty_read_uses_quiet_post_exit_as_eof():
    async def _run() -> None:
        process = Mock(returncode=0)
        native = Mock()
        native.read.side_effect = socket.timeout("timed out")
        handle = WindowsPtyProcessHandle(process=process, native_pty=native)

        output = await WindowsPtyBackend().read(handle)

        assert output == b""

    asyncio.run(_run())


def test_windows_pty_read_ignores_empty_live_poll():
    async def _run() -> None:
        process = Mock(returncode=None)
        native = Mock()
        native.read.side_effect = ["", "ready"]
        handle = WindowsPtyProcessHandle(process=process, native_pty=native)

        output = await WindowsPtyBackend().read(handle)

        assert output == b"ready"

    asyncio.run(_run())


def test_windows_pty_process_prefers_child_exit_status_over_relay_liveness():
    native = Mock(pid=123, exitstatus=0)
    native.isalive.return_value = True

    process = _WinPtyAsyncProcess(native)

    assert process.returncode == 0
    native.isalive.assert_not_called()


def test_windows_pty_process_wait_polls_child_exit_status():
    async def _run() -> None:
        class Native:
            pid = 123

            def __init__(self):
                self.statuses = iter((None, 0))

            @property
            def exitstatus(self):
                return next(self.statuses)

            def isalive(self):
                return True

        native = Native()
        process = _WinPtyAsyncProcess(native)

        assert await process.wait() == 0

    asyncio.run(_run())
