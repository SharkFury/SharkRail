"""Platform execution backends.

The public runtime depends on intent-level operations here instead of directly
depending on POSIX signals or Windows process flags.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from .models import CommandSpec
from .windows import WindowsJob

if os.name != "nt":
    import fcntl
    import pty
    import struct
    import termios


@dataclass
class ProcessHandle:
    process: asyncio.subprocess.Process
    stdin_closed: bool = False

    @property
    def pid(self) -> int:
        return self.process.pid


@dataclass
class PtyProcessHandle(ProcessHandle):
    master_fd: int = -1
    output_closed: bool = False


@dataclass
class WindowsProcessHandle(ProcessHandle):
    job: WindowsJob | None = None


@dataclass
class WindowsPtyProcessHandle(PtyProcessHandle):
    native_pty: Any = None
    job: WindowsJob | None = None


class CancellationStep(str, Enum):
    INTERRUPT = "interrupt"
    TERMINATE = "terminate"
    KILL_TREE = "kill_tree"


@dataclass(frozen=True)
class CancellationPolicy:
    interrupt_grace_ms: int = 1000
    terminate_grace_ms: int = 1000
    skip_interrupt: bool = False

    def validate(self) -> None:
        if self.interrupt_grace_ms < 0 or self.terminate_grace_ms < 0:
            raise ValueError("cancellation grace periods must be non-negative")


class ExecutionBackend(ABC):
    @abstractmethod
    async def start(self, spec: CommandSpec) -> ProcessHandle:
        raise NotImplementedError

    @abstractmethod
    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close_stdin(self, handle: ProcessHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    async def interrupt(self, handle: ProcessHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    async def terminate(self, handle: ProcessHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    async def kill_tree(self, handle: ProcessHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    async def dispose(self, handle: ProcessHandle) -> None:
        raise NotImplementedError


class PipeBackend(ExecutionBackend):
    """Pipe execution using a dedicated process group for tree operations."""

    async def start(self, spec: CommandSpec) -> ProcessHandle:
        environment = None
        if spec.env is not None:
            environment = os.environ.copy()
            environment.update(spec.env)
        kwargs: dict[str, object] = {
            "cwd": spec.cwd,
            "env": environment,
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(*spec.argv_list, **kwargs)
        return ProcessHandle(process=process)

    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        if handle.stdin_closed or handle.process.stdin is None:
            raise RuntimeError("stdin is closed")
        handle.process.stdin.write(data)
        await handle.process.stdin.drain()

    async def close_stdin(self, handle: ProcessHandle) -> None:
        if handle.stdin_closed:
            return
        handle.stdin_closed = True
        if handle.process.stdin is not None:
            handle.process.stdin.close()
            await handle.process.stdin.wait_closed()

    async def interrupt(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is not None:
            return
        if os.name == "nt":
            handle.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(handle.pid, signal.SIGINT)

    async def terminate(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is not None:
            return
        if os.name == "nt":
            handle.process.terminate()
        else:
            os.killpg(handle.pid, signal.SIGTERM)

    async def kill_tree(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is not None:
            return
        if os.name == "nt":
            # taskkill is available on supported Windows versions and provides
            # tree semantics until the native Job Object backend lands.
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(handle.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            if handle.process.returncode is None:
                handle.process.kill()
        else:
            try:
                os.killpg(handle.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def dispose(self, handle: ProcessHandle) -> None:
        return None


class WindowsPipeBackend(PipeBackend):
    """Windows pipe backend backed by a kill-on-close Job Object."""

    async def start(self, spec: CommandSpec) -> WindowsProcessHandle:
        handle = await super().start(spec)
        job = WindowsJob()
        try:
            job.assign(handle.pid)
        except BaseException:
            job.close()
            handle.process.kill()
            await handle.process.wait()
            raise
        return WindowsProcessHandle(process=handle.process, job=job)

    async def kill_tree(self, handle: ProcessHandle) -> None:
        if isinstance(handle, WindowsProcessHandle) and handle.job is not None:
            handle.job.terminate()
            return
        await super().kill_tree(handle)

    async def dispose(self, handle: ProcessHandle) -> None:
        if isinstance(handle, WindowsProcessHandle) and handle.job is not None:
            handle.job.close()
            handle.job = None


def pipe_backend() -> PipeBackend:
    return WindowsPipeBackend() if os.name == "nt" else PipeBackend()


class PtyBackend(ExecutionBackend):
    """Native POSIX PTY backend with a merged terminal stream."""

    async def start(self, spec: CommandSpec) -> PtyProcessHandle:
        if os.name == "nt":
            raise NotImplementedError("ConPTY backend is not available in this build")
        environment = None
        if spec.env is not None:
            environment = os.environ.copy()
            environment.update(spec.env)
        master_fd, slave_fd = pty.openpty()
        try:
            process = await asyncio.create_subprocess_exec(
                *spec.argv_list,
                cwd=spec.cwd,
                env=environment,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        return PtyProcessHandle(process=process, master_fd=master_fd)

    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        pty_handle = _as_pty(handle)
        if pty_handle.stdin_closed:
            raise RuntimeError("stdin is closed")
        await asyncio.to_thread(os.write, pty_handle.master_fd, data)

    async def close_stdin(self, handle: ProcessHandle) -> None:
        pty_handle = _as_pty(handle)
        if pty_handle.stdin_closed:
            return
        pty_handle.stdin_closed = True
        # POSIX terminal EOF (VEOF) preserves the output side of the PTY.
        await asyncio.to_thread(os.write, pty_handle.master_fd, b"\x04")

    async def interrupt(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is None:
            os.killpg(handle.pid, signal.SIGINT)

    async def terminate(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is None:
            os.killpg(handle.pid, signal.SIGTERM)

    async def kill_tree(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is not None:
            return
        try:
            os.killpg(handle.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def read(self, handle: PtyProcessHandle, size: int = 65536) -> bytes:
        if handle.output_closed:
            return b""
        try:
            return await asyncio.to_thread(os.read, handle.master_fd, size)
        except OSError as err:
            # Linux returns EIO after the PTY slave closes; macOS commonly
            # returns an empty read. Both represent terminal EOF.
            if err.errno == 5:
                return b""
            raise

    async def resize(self, handle: PtyProcessHandle, cols: int, rows: int) -> None:
        if cols <= 0 or rows <= 0:
            raise ValueError("terminal dimensions must be positive")
        dimensions = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(handle.master_fd, termios.TIOCSWINSZ, dimensions)

    async def dispose(self, handle: PtyProcessHandle) -> None:
        if handle.output_closed:
            return
        handle.output_closed = True
        os.close(handle.master_fd)


class _WinPtyAsyncProcess:
    """Small asyncio-compatible facade around pywinpty's blocking process."""

    stdin = None
    stdout = None
    stderr = None

    def __init__(self, native: Any) -> None:
        self.native = native
        self.pid = native.pid
        self._returncode: int | None = None

    @property
    def returncode(self) -> int | None:
        if self._returncode is None and not self.native.isalive():
            self._returncode = self.native.exitstatus or 0
        return self._returncode

    async def wait(self) -> int:
        self._returncode = await asyncio.to_thread(self.native.wait)
        return self._returncode


class WindowsPtyBackend(ExecutionBackend):
    """ConPTY backend powered by pywinpty on supported Windows systems."""

    async def start(self, spec: CommandSpec) -> WindowsPtyProcessHandle:
        if os.name != "nt":
            raise NotImplementedError("ConPTY is only available on Windows")
        try:
            from winpty import PtyProcess
        except ImportError as err:
            raise RuntimeError("Windows PTY support requires pywinpty") from err
        environment = os.environ.copy()
        if spec.env is not None:
            environment.update(spec.env)
        native = await asyncio.to_thread(
            PtyProcess.spawn,
            spec.argv_list,
            cwd=spec.cwd,
            env=environment,
            dimensions=(24, 80),
        )
        process = _WinPtyAsyncProcess(native)
        job = WindowsJob()
        try:
            job.assign(process.pid)
        except BaseException:
            native.close(force=True)
            job.close()
            raise
        return WindowsPtyProcessHandle(process=process, native_pty=native, job=job)

    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.stdin_closed:
            raise RuntimeError("stdin is closed")
        await asyncio.to_thread(pty_handle.native_pty.write, data.decode("utf-8", errors="replace"))

    async def close_stdin(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if not pty_handle.stdin_closed:
            pty_handle.stdin_closed = True
            await asyncio.to_thread(pty_handle.native_pty.sendeof)

    async def interrupt(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.process.returncode is None:
            await asyncio.to_thread(pty_handle.native_pty.sendintr)

    async def terminate(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.process.returncode is None:
            await asyncio.to_thread(pty_handle.native_pty.terminate, False)

    async def kill_tree(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.job is not None:
            pty_handle.job.terminate()
        elif pty_handle.process.returncode is None:
            await asyncio.to_thread(pty_handle.native_pty.terminate, True)

    async def read(self, handle: WindowsPtyProcessHandle, size: int = 65536) -> bytes:
        del size  # pywinpty 3.x returns all currently available characters.
        if handle.output_closed:
            return b""
        try:
            text = await asyncio.to_thread(handle.native_pty.read)
        except EOFError:
            return b""
        return text.encode("utf-8")

    async def resize(self, handle: WindowsPtyProcessHandle, cols: int, rows: int) -> None:
        if cols <= 0 or rows <= 0:
            raise ValueError("terminal dimensions must be positive")
        await asyncio.to_thread(handle.native_pty.setwinsize, rows, cols)

    async def dispose(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.output_closed:
            return
        pty_handle.output_closed = True
        if pty_handle.native_pty.isalive():
            await asyncio.to_thread(pty_handle.native_pty.close, True)
        if pty_handle.job is not None:
            pty_handle.job.close()
            pty_handle.job = None


def _as_pty(handle: ProcessHandle) -> PtyProcessHandle:
    if not isinstance(handle, PtyProcessHandle):
        raise TypeError("PTY operation requires a PtyProcessHandle")
    return handle


def _as_windows_pty(handle: ProcessHandle) -> WindowsPtyProcessHandle:
    if not isinstance(handle, WindowsPtyProcessHandle):
        raise TypeError("ConPTY operation requires a WindowsPtyProcessHandle")
    return handle


async def read_pty_output(backend: Any, handle: PtyProcessHandle) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = await backend.read(handle)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def pty_backend() -> ExecutionBackend:
    return WindowsPtyBackend() if os.name == "nt" else PtyBackend()


async def wait_for_exit(handle: ProcessHandle, timeout: Optional[float] = None) -> bool:
    """Wait for exit and return False when the deadline expires."""
    try:
        if timeout is None:
            await handle.process.wait()
        else:
            await asyncio.wait_for(handle.process.wait(), timeout)
    except asyncio.TimeoutError:
        return False
    return True


async def cancel_process(
    backend: ExecutionBackend,
    handle: ProcessHandle,
    policy: Optional[CancellationPolicy] = None,
) -> tuple[CancellationStep, ...]:
    """Apply portable interrupt -> terminate -> kill-tree escalation."""
    policy = policy or CancellationPolicy()
    policy.validate()
    steps: list[CancellationStep] = []
    if handle.process.returncode is not None:
        return ()

    if not policy.skip_interrupt:
        steps.append(CancellationStep.INTERRUPT)
        await backend.interrupt(handle)
        if await wait_for_exit(handle, policy.interrupt_grace_ms / 1000):
            return tuple(steps)

    steps.append(CancellationStep.TERMINATE)
    await backend.terminate(handle)
    if await wait_for_exit(handle, policy.terminate_grace_ms / 1000):
        return tuple(steps)

    steps.append(CancellationStep.KILL_TREE)
    await backend.kill_tree(handle)
    await wait_for_exit(handle)
    return tuple(steps)
