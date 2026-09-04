import asyncio
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sharkrail.backends import (
    PipeBackend,
    ProcessHandle,
    PtyBackend,
    WindowsPipeBackend,
    WindowsProcessHandle,
    WindowsPtyBackend,
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
