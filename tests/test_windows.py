import asyncio
import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sharkrail.backends import (
    PipeBackend,
    ProcessHandle,
    PtyBackend,
    WindowsPipeBackend,
    WindowsProcessHandle,
    WindowsPtyBackend,
    WindowsPtyProcessHandle,
    _WinPtyAsyncProcess,
    pipe_backend,
    pty_backend,
)
from sharkrail.models import CommandSpec, ResourceLimits
from sharkrail.windows import WindowsJob


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


@pytest.mark.skipif(os.name == "nt", reason="non-Windows guard assertion")
def test_windows_job_has_explicit_platform_guard():
    with pytest.raises(OSError, match="only available on Windows"):
        WindowsJob()


def test_platform_pty_backend_selection():
    backend = pty_backend()
    if os.name == "nt":
        assert isinstance(backend, WindowsPtyBackend)
    else:
        assert type(backend) is PtyBackend


def test_windows_pipe_falls_back_when_job_assignment_is_unavailable():
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
            patch("sharkrail.backends.WindowsJob", return_value=job),
        ):
            handle = await backend.start(CommandSpec("tool", ()))

        assert isinstance(handle, WindowsProcessHandle)
        assert handle.job is None
        assert process.killed is False
        job.close.assert_called_once_with()

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
            patch("sharkrail.backends.WindowsJob", return_value=job),
            pytest.raises(OSError, match="nested Job assignment"),
        ):
            await backend.start(spec)

        assert process.killed is True
        job.close.assert_called_once_with()

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
            patch("sharkrail.backends.os.name", "nt"),
            patch("sharkrail.backends.WindowsJob", return_value=job),
        ):
            handle = await backend.start(CommandSpec("tool", ()))

        assert isinstance(handle, WindowsPtyProcessHandle)
        native.fileobj.settimeout.assert_called_once_with(backend._read_poll_seconds)
        job.assign.assert_called_once_with(123)

    asyncio.run(_run())


def test_windows_pty_read_uses_quiet_post_exit_as_eof():
    async def _run() -> None:
        process = Mock(returncode=0)
        native = Mock()
        native.read.side_effect = TimeoutError
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
