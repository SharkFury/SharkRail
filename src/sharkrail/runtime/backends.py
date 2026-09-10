"""Platform execution backends.

The public runtime depends on intent-level operations here instead of directly
depending on POSIX signals or Windows process flags.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import socket
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Awaitable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from ..core.models import CommandSpec
from .process_identity import process_birth_identity
from .windows import WindowsJob

if os.name != "nt":
    import fcntl
    import pty
    import resource
    import struct
    import termios

_WINDOWS_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
_WINDOWS_CREATE_SUSPENDED = getattr(subprocess, "CREATE_SUSPENDED", 0x4)


def _child_environment(spec: CommandSpec) -> dict[str, str]:
    """Build a child environment with the Windows process bootstrap minimum."""

    environment = os.environ.copy() if spec.inherit_env else {}
    if not spec.inherit_env and os.name == "nt":
        # CreateProcess and the Windows loader require SystemRoot for some
        # executables. Keeping this OS bootstrap value does not re-enable
        # arbitrary parent-environment inheritance.
        system_root = os.environ.get("SYSTEMROOT")
        if system_root:
            environment["SYSTEMROOT"] = system_root
    if spec.env is not None:
        environment.update(spec.env)
    return environment


def _windows_terminal_input(data: bytes) -> bytes:
    """Translate portable LF input to ConPTY enter sequences without doubling CRLF."""

    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


@dataclass
class ProcessHandle:
    process: Any
    stdin_closed: bool = False
    process_tree: str = "unknown"
    birth_identity: str | None = None
    degraded_reasons: tuple[str, ...] = ()
    _disposed: bool = field(default=False, init=False, repr=False, compare=False)
    _tree_killed: bool = field(default=False, init=False, repr=False, compare=False)
    _tree_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False, compare=False
    )
    _dispose_task: Any = field(default=None, init=False, repr=False, compare=False)
    _ownership_id: str | None = field(
        default=None, init=False, repr=False, compare=False
    )

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
    broker_job: WindowsJob | None = None
    broker: Any = None
    broker_control: Any = None
    broker_output: Any = None
    _write_future: Any = field(default=None, init=False, repr=False, compare=False)
    _write_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )
    _control_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False, compare=False
    )
    _control_failed: bool = field(default=False, init=False, repr=False, compare=False)
    _closing: bool = field(default=False, init=False, repr=False, compare=False)


class CancellationStep(str, Enum):
    INTERRUPT = "interrupt"
    TERMINATE = "terminate"
    KILL_TREE = "kill_tree"


@dataclass(frozen=True)
class CancellationPolicy:
    interrupt_grace_ms: int = 1000
    terminate_grace_ms: int = 1000
    kill_tree_grace_ms: int = 2000
    skip_interrupt: bool = False

    def validate(self) -> None:
        if (
            self.interrupt_grace_ms < 0
            or self.terminate_grace_ms < 0
            or self.kill_tree_grace_ms <= 0
        ):
            raise ValueError(
                "interrupt and terminate grace periods must be non-negative; "
                "kill-tree grace period must be positive"
            )


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
        environment = _child_environment(spec)
        kwargs: dict[str, Any] = {
            "cwd": spec.cwd,
            "env": environment,
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            kwargs["creationflags"] = self._windows_creation_flags()
        else:
            kwargs["start_new_session"] = True
            kwargs["preexec_fn"] = _resource_limiter(spec)
        process = await asyncio.create_subprocess_exec(*spec.argv_list, **kwargs)
        return ProcessHandle(
            process=process,
            process_tree="taskkill_fallback" if os.name == "nt" else "process_group",
            birth_identity=process_birth_identity(process.pid),
        )

    def _windows_creation_flags(self) -> int:
        return _WINDOWS_NEW_PROCESS_GROUP

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
            handle.process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
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
        async with handle._tree_lock:
            if handle._tree_killed:
                return
            if os.name == "nt":
                # taskkill is available on supported Windows versions and provides
                # tree semantics when Job assignment is unavailable. It is unsafe
                # to target an exited root by its reusable PID, and taskkill cannot
                # reliably reconstruct descendants after that root disappears.
                if handle.process.returncode is not None:
                    handle._tree_killed = True
                    return
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
                    try:
                        handle.process.kill()
                    except ProcessLookupError:
                        pass
            else:
                try:
                    os.killpg(handle.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            handle._tree_killed = True

    async def dispose(self, handle: ProcessHandle) -> None:
        if handle._disposed:
            return
        # A root process can exit while descendants in a stably identified
        # process group or Job continue running. Disposal is the final
        # ownership boundary for those trees; the taskkill fallback itself
        # guards against targeting an exited, reusable root PID.
        try:
            if handle.process_tree != "unknown":
                await self.kill_tree(handle)
        finally:
            if not handle.stdin_closed and handle.process.stdin is not None:
                handle.stdin_closed = True
                handle.process.stdin.close()
                try:
                    await handle.process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
        handle._disposed = True


class WindowsPipeBackend(PipeBackend):
    """Windows pipe backend backed by a kill-on-close Job Object."""

    def _windows_creation_flags(self) -> int:
        # The user's entry point cannot execute or create descendants before
        # the root process has been placed in its owning Job Object.
        return _WINDOWS_NEW_PROCESS_GROUP | _WINDOWS_CREATE_SUSPENDED

    async def start(self, spec: CommandSpec) -> WindowsProcessHandle:
        # Acquire tree ownership before launching. If Job construction fails,
        # no process exists yet and therefore no fast-exiting child can escape
        # before it has an owner.
        job = WindowsJob(
            memory_bytes=spec.resources.memory_bytes,
            cpu_time_seconds=spec.resources.cpu_time_seconds,
            process_count=spec.resources.process_count,
        )
        try:
            handle = await super().start(spec)
        except BaseException:
            job.close()
            raise
        try:
            job.assign(handle.pid)
        except BaseException:
            try:
                job.close()
            except OSError:
                pass
            await self._reap_unassigned_process(handle)
            raise
        try:
            job.resume(handle.pid)
        except BaseException:
            await self._reap_assigned_process(handle, job)
            raise
        return WindowsProcessHandle(
            process=handle.process,
            process_tree="job_object",
            birth_identity=handle.birth_identity,
            job=job,
        )

    async def kill_tree(self, handle: ProcessHandle) -> None:
        if isinstance(handle, WindowsProcessHandle) and handle.job is not None:
            async with handle._tree_lock:
                if handle._tree_killed:
                    return
                handle.job.terminate()
                emptied = await asyncio.to_thread(handle.job.wait_empty, 1.0)
                if not emptied:
                    raise TimeoutError("Windows Job still contains active processes")
                handle._tree_killed = True
            return
        await super().kill_tree(handle)

    async def dispose(self, handle: ProcessHandle) -> None:
        try:
            await super().dispose(handle)
        finally:
            if isinstance(handle, WindowsProcessHandle) and handle.job is not None:
                handle.job.close()
                handle.job = None

    async def _reap_unassigned_process(self, handle: ProcessHandle) -> None:
        """Reap a process that could not be placed in its owning Job Object."""

        try:
            await super().kill_tree(handle)
        finally:
            if handle.process.returncode is None:
                try:
                    handle.process.kill()
                except ProcessLookupError:
                    pass
            await handle.process.wait()

    @staticmethod
    async def _reap_assigned_process(handle: ProcessHandle, job: WindowsJob) -> None:
        """Fail closed if a Job-owned suspended process cannot be resumed."""

        try:
            job.terminate()
        except OSError:
            pass
        finally:
            try:
                job.close()
            except OSError:
                pass
        if handle.process.returncode is None:
            try:
                handle.process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(handle.process.wait(), 1.0)
        except asyncio.TimeoutError:
            pass


def pipe_backend() -> PipeBackend:
    return WindowsPipeBackend() if os.name == "nt" else PipeBackend()


class PtyBackend(ExecutionBackend):
    """Native POSIX PTY backend with a merged terminal stream."""

    async def start(self, spec: CommandSpec) -> PtyProcessHandle:
        if os.name == "nt":
            raise NotImplementedError("ConPTY backend is not available in this build")
        environment = _child_environment(spec)
        master_fd, slave_fd = pty.openpty()
        try:
            os.set_blocking(master_fd, False)
            process = await asyncio.create_subprocess_exec(
                *spec.argv_list,
                cwd=spec.cwd,
                env=environment,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                preexec_fn=_resource_limiter(spec),
            )
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        return PtyProcessHandle(
            process=process,
            process_tree="process_group",
            birth_identity=process_birth_identity(process.pid),
            master_fd=master_fd,
        )

    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        pty_handle = _as_pty(handle)
        if pty_handle.stdin_closed:
            raise RuntimeError("stdin is closed")
        await _write_fd(pty_handle.master_fd, data)

    async def close_stdin(self, handle: ProcessHandle) -> None:
        pty_handle = _as_pty(handle)
        if pty_handle.stdin_closed:
            return
        attributes = termios.tcgetattr(pty_handle.master_fd)
        if not attributes[3] & termios.ICANON:
            raise RuntimeError(
                "stdin EOF is unavailable for a POSIX PTY in non-canonical mode"
            )
        veof_value = attributes[6][termios.VEOF]
        numeric_veof = (
            veof_value
            if isinstance(veof_value, int)
            else int.from_bytes(bytes(veof_value), "little")
        )
        if numeric_veof == os.fpathconf(pty_handle.master_fd, "PC_VDISABLE"):
            raise RuntimeError("stdin EOF is disabled for this POSIX PTY")
        veof = (
            bytes((veof_value,)) if isinstance(veof_value, int) else bytes(veof_value)
        )
        # One VEOF submits an unterminated canonical line and the second
        # presents EOF to the following read. Both are required for a
        # deterministic close_stdin() operation.
        await _write_fd(pty_handle.master_fd, veof + veof)
        pty_handle.stdin_closed = True

    async def interrupt(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is None:
            os.killpg(handle.pid, signal.SIGINT)

    async def terminate(self, handle: ProcessHandle) -> None:
        if handle.process.returncode is None:
            os.killpg(handle.pid, signal.SIGTERM)

    async def kill_tree(self, handle: ProcessHandle) -> None:
        async with handle._tree_lock:
            if handle._tree_killed:
                return
            try:
                os.killpg(handle.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            handle._tree_killed = True

    async def read(self, handle: PtyProcessHandle, size: int = 65536) -> bytes:
        if handle.output_closed:
            return b""
        while True:
            try:
                return os.read(handle.master_fd, size)
            except BlockingIOError:
                await _wait_for_fd(handle.master_fd, writable=False)
            except InterruptedError:
                continue
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

    async def dispose(self, handle: ProcessHandle) -> None:
        pty_handle = _as_pty(handle)
        if pty_handle._disposed:
            return
        try:
            await self.kill_tree(handle)
        finally:
            if not pty_handle.output_closed:
                pty_handle.output_closed = True
                os.close(pty_handle.master_fd)
        pty_handle._disposed = True


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
        if self._returncode is None:
            # On ConPTY, pywinpty can keep its relay alive after the child has
            # exited. get_exitstatus() is the authoritative child status and
            # must be checked before the broader PTY liveness flag.
            exitstatus = self.native.exitstatus
            if exitstatus is not None:
                self._returncode = exitstatus
            elif not self.native.isalive():
                self._returncode = self.native.exitstatus or 0
        return self._returncode

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        assert self._returncode is not None
        return self._returncode


class _BrokeredWinPtyProcess:
    """Async process facade whose ConPTY is owned by a killable broker."""

    stdin = None
    stdout = None
    stderr = None

    def __init__(self, pid: int, broker: Any, status: Any) -> None:
        self.pid = pid
        self._broker = broker
        self._status = status
        self._returncode: int | None = None

    @property
    def returncode(self) -> int | None:
        try:
            while self._returncode is None and self._status.poll():
                message = self._status.recv()
                if isinstance(message, tuple) and message[:1] == ("exit",):
                    self._returncode = int(message[1])
        except (EOFError, OSError):
            pass
        if self._returncode is None and self._broker.exitcode is not None:
            self._returncode = int(self._broker.exitcode)
        return self._returncode

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        assert self._returncode is not None
        return self._returncode


def _conpty_broker_main(control: Any, output: Any, status: Any) -> None:
    """Own pywinpty in a helper process that a Windows Job can terminate."""

    native: Any = None
    user_job: WindowsJob | None = None
    try:
        try:
            request = control.recv()
        except (BrokenPipeError, EOFError, OSError):
            return
        if not isinstance(request, tuple) or request[:1] != ("start",):
            raise RuntimeError("invalid ConPTY broker bootstrap request")
        (
            _,
            argv,
            cwd,
            environment,
            dimensions,
            read_poll_seconds,
            user_job_handle,
        ) = request
        from winpty import PtyProcess

        try:
            user_job = WindowsJob.from_handle(int(user_job_handle))
            native = PtyProcess.spawn(
                argv,
                cwd=cwd,
                env=environment,
                dimensions=dimensions,
            )
            user_job.assign(native.pid)
        except BaseException as err:  # noqa: BLE001 - process boundary
            _send_broker_error(control, err)
            return
        relay = getattr(native, "fileobj", None)
        if relay is not None and hasattr(relay, "settimeout"):
            relay.settimeout(read_poll_seconds)
        control.send(("ok", native.pid))

        def relay_output() -> None:
            exit_sent = False
            try:
                while True:
                    try:
                        text = native.read()
                    except EOFError:
                        break
                    except (TimeoutError, socket.timeout):
                        text = ""
                    if text:
                        output.send(("data", text))
                    exit_code = native.exitstatus
                    if exit_code is not None:
                        if not exit_sent:
                            status.send(("exit", int(exit_code)))
                            exit_sent = True
                        if not text:
                            break
            except BaseException as err:  # noqa: BLE001 - process boundary
                try:
                    output.send(("error", type(err).__name__, str(err)))
                except (BrokenPipeError, EOFError, OSError):
                    pass
            finally:
                try:
                    if not exit_sent and native.exitstatus is not None:
                        status.send(("exit", int(native.exitstatus)))
                    output.send(("eof",))
                except (BrokenPipeError, EOFError, OSError):
                    pass

        threading.Thread(
            target=relay_output,
            name="sharkrail-conpty-broker-output",
            daemon=True,
        ).start()

        while True:
            request = control.recv()
            if not isinstance(request, tuple) or len(request) != 2:
                raise RuntimeError("invalid ConPTY broker request")
            operation, args = request
            try:
                if operation == "write":
                    result = native.write(*args)
                elif operation == "sendeof":
                    result = native.sendeof(*args)
                elif operation == "sendintr":
                    result = native.sendintr(*args)
                elif operation == "terminate":
                    result = native.terminate(*args)
                elif operation == "setwinsize":
                    result = native.setwinsize(*args)
                elif operation == "close":
                    result = native.close(*args)
                    control.send(("ok", result))
                    break
                else:
                    raise RuntimeError(f"unknown ConPTY broker operation: {operation}")
            except BaseException as err:  # noqa: BLE001 - process boundary
                _send_broker_error(control, err)
            else:
                control.send(("ok", result))
    except (BrokenPipeError, EOFError):
        pass
    except BaseException as err:  # noqa: BLE001 - process boundary
        _send_broker_error(control, err)
    finally:
        if native is not None:
            try:
                native.close(force=True)
            except BaseException:  # noqa: BLE001 - process teardown
                native = None
        if user_job is not None:
            try:
                user_job.close()
            except OSError:
                pass
        for connection in (control, output, status):
            try:
                connection.close()
            except OSError:
                pass


def _send_broker_error(connection: Any, error: BaseException) -> None:
    """Send a data-only error envelope without attempting to pickle exceptions."""

    try:
        connection.send(
            (
                "error",
                type(error).__name__,
                str(error),
                getattr(error, "errno", None),
                getattr(error, "winerror", None),
            )
        )
    except (BrokenPipeError, EOFError, OSError):
        pass


class WindowsPtyBackend(ExecutionBackend):
    """ConPTY backend powered by pywinpty on supported Windows systems."""

    _read_poll_seconds = 0.1
    _spawn_timeout_seconds = 30.0

    async def start(self, spec: CommandSpec) -> WindowsPtyProcessHandle:
        if os.name != "nt":
            raise NotImplementedError("ConPTY is only available on Windows")
        if sys.platform == "win32":  # pragma: no cover - exercised by Windows CI
            return await self._start_brokered(spec)
        # Non-Windows unit tests patch os.name and provide an in-process fake
        # winpty module. Keep that seam without using process-global executors.
        return await self._start_test_double(spec)

    async def _start_brokered(self, spec: CommandSpec) -> WindowsPtyProcessHandle:
        environment = _child_environment(spec)
        # Keep the broker in an unmetered ownership Job and place only the user
        # process tree in the nested resource Job. This preserves kill-on-close
        # ownership without charging broker memory or CPU to the user's limits.
        broker_job = WindowsJob()
        try:
            user_job = WindowsJob(
                memory_bytes=spec.resources.memory_bytes,
                cpu_time_seconds=spec.resources.cpu_time_seconds,
                process_count=spec.resources.process_count,
            )
        except BaseException:
            broker_job.close()
            raise
        context = multiprocessing.get_context("spawn")
        parent_control, child_control = context.Pipe(duplex=True)
        parent_output, child_output = context.Pipe(duplex=False)
        parent_status, child_status = context.Pipe(duplex=False)
        broker = context.Process(
            target=_conpty_broker_main,
            args=(child_control, child_output, child_status),
            name="sharkrail-conpty-broker",
            daemon=True,
        )
        try:
            broker.start()
            child_control.close()
            child_output.close()
            child_status.close()
            broker_pid = broker.pid
            if broker_pid is None:
                raise RuntimeError("ConPTY broker did not publish a process ID")
            broker_job.assign(broker_pid)
            user_job_handle = user_job.duplicate_to_process(broker_pid)
            parent_control.send(
                (
                    "start",
                    spec.argv_list,
                    spec.cwd,
                    environment,
                    (24, 80),
                    self._read_poll_seconds,
                    user_job_handle,
                )
            )
            response = await asyncio.wait_for(
                _receive_broker_message(parent_control, broker),
                self._spawn_timeout_seconds,
            )
            if response[:1] != ("ok",):
                _raise_broker_error(response)
            process = _BrokeredWinPtyProcess(int(response[1]), broker, parent_status)
            return WindowsPtyProcessHandle(
                process=process,
                process_tree="job_object",
                birth_identity=process_birth_identity(process.pid),
                native_pty=None,
                job=user_job,
                broker_job=broker_job,
                broker=broker,
                broker_control=parent_control,
                broker_output=parent_output,
            )
        except BaseException:
            try:
                user_job.terminate()
            finally:
                # Both kill-on-close boundaries are released before any join,
                # so cancellation cannot strand the broker or user tree.
                user_job.close()
                broker_job.close()
                for connection in (
                    parent_control,
                    parent_output,
                    parent_status,
                    child_control,
                    child_output,
                    child_status,
                ):
                    try:
                        connection.close()
                    except OSError:
                        pass
            await asyncio.to_thread(_reap_broker_process, broker)
            raise

    async def _start_test_double(self, spec: CommandSpec) -> WindowsPtyProcessHandle:
        try:
            from winpty import PtyProcess
        except ImportError as err:
            raise RuntimeError("Windows PTY support requires pywinpty") from err
        environment = _child_environment(spec)
        # As with the pipe backend, construct the kill-on-close owner before
        # starting a process so constructor failure cannot leak a process tree.
        job = WindowsJob(
            memory_bytes=spec.resources.memory_bytes,
            cpu_time_seconds=spec.resources.cpu_time_seconds,
            process_count=spec.resources.process_count,
        )
        try:
            spawn_future = _submit_conpty_start(
                PtyProcess.spawn,
                spec.argv_list,
                cwd=spec.cwd,
                env=environment,
                dimensions=(24, 80),
            )
        except BaseException:
            job.close()
            raise
        abandoned = threading.Event()
        cleanup_lock = threading.Lock()
        cleaned = False

        def cleanup_abandoned_spawn(completed: Future[Any]) -> None:
            nonlocal cleaned
            if not abandoned.is_set():
                return
            with cleanup_lock:
                if cleaned:
                    return
                cleaned = True
            try:
                spawned = completed.result()
            except BaseException:  # noqa: BLE001 - native worker boundary
                job.close()
                return
            try:
                spawned.close(force=True)
            finally:
                job.close()

        spawn_future.add_done_callback(cleanup_abandoned_spawn)
        try:
            native = await asyncio.shield(asyncio.wrap_future(spawn_future))
        except BaseException:
            abandoned.set()
            if not spawn_future.cancel() and spawn_future.done():
                cleanup_abandoned_spawn(spawn_future)
            raise
        # pywinpty can leave its relay socket open after the child exits, so a
        # permanently blocking read cannot reliably observe terminal EOF.
        # A short socket deadline lets read() combine output availability with
        # the authoritative child exit status.
        try:
            relay = getattr(native, "fileobj", None)
            if relay is not None and hasattr(relay, "settimeout"):
                relay.settimeout(self._read_poll_seconds)
            process = _WinPtyAsyncProcess(native)
            job.assign(process.pid)
        except BaseException:
            try:
                await asyncio.to_thread(native.close, force=True)
            finally:
                job.close()
            raise
        return WindowsPtyProcessHandle(
            process=process,
            process_tree="job_object",
            birth_identity=process_birth_identity(process.pid),
            degraded_reasons=(
                (
                    "ConPTY Job assignment occurs after pywinpty spawn; use pipe "
                    "mode when pre-execution Job containment is required"
                ),
            ),
            native_pty=native,
            job=job,
        )

    async def write(self, handle: ProcessHandle, data: bytes) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.stdin_closed or pty_handle._closing:
            raise RuntimeError("stdin is closed")
        future: Any
        if pty_handle.broker is not None:
            with pty_handle._write_lock:
                pending = pty_handle._write_future
                if pending is not None and not pending.done():
                    raise RuntimeError("a previous ConPTY write is still pending")
                future = asyncio.create_task(
                    self._broker_request(
                        pty_handle,
                        "write",
                        _windows_terminal_input(data).decode("utf-8", errors="replace"),
                    )
                )
                pty_handle._write_future = future
        else:
            with pty_handle._write_lock:
                pending = pty_handle._write_future
                if pending is not None and not pending.done():
                    raise RuntimeError("a previous ConPTY write is still pending")
                future = _submit_conpty_write(
                    pty_handle.native_pty.write,
                    _windows_terminal_input(data).decode("utf-8", errors="replace"),
                )
                pty_handle._write_future = future

        def clear_write(completed: Any) -> None:
            with pty_handle._write_lock:
                if pty_handle._write_future is completed:
                    pty_handle._write_future = None
            if isinstance(completed, asyncio.Future) and not completed.cancelled():
                completed.exception()

        future.add_done_callback(clear_write)
        if isinstance(future, asyncio.Future):
            await asyncio.shield(future)
        else:
            await asyncio.shield(asyncio.wrap_future(future))

    async def close_stdin(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if not pty_handle.stdin_closed:
            if pty_handle._closing:
                raise RuntimeError("ConPTY is closing")
            if pty_handle.broker is not None:
                await self._broker_request(pty_handle, "sendeof")
            else:
                await asyncio.to_thread(pty_handle.native_pty.sendeof)
            pty_handle.stdin_closed = True

    async def interrupt(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.process.returncode is None:
            if pty_handle.broker is not None:
                await self._broker_request(pty_handle, "sendintr")
            else:
                await asyncio.to_thread(pty_handle.native_pty.sendintr)

    async def terminate(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle.process.returncode is None:
            if pty_handle.broker is not None:
                await self._broker_request(pty_handle, "terminate", False)
            else:
                await asyncio.to_thread(pty_handle.native_pty.terminate, False)

    async def kill_tree(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        async with handle._tree_lock:
            if handle._tree_killed:
                return
            if pty_handle.job is not None:
                pty_handle.job.terminate()
                emptied = await asyncio.to_thread(pty_handle.job.wait_empty, 1.0)
                if not emptied:
                    raise TimeoutError("Windows Job still contains active processes")
            elif pty_handle.process.returncode is None:
                await asyncio.to_thread(pty_handle.native_pty.terminate, True)
            handle._tree_killed = True

    async def read(self, handle: WindowsPtyProcessHandle, size: int = 65536) -> bytes:
        del size  # pywinpty 3.x returns all currently available characters.
        if handle.output_closed:
            return b""
        if handle.broker is not None:
            assert handle.broker_output is not None
            while True:
                try:
                    available = handle.broker_output.poll()
                    message = handle.broker_output.recv() if available else None
                except (BrokenPipeError, EOFError, OSError):
                    if (
                        handle.process.returncode is not None
                        or handle.broker.exitcode is not None
                    ):
                        return b""
                    raise
                if message is not None:
                    if message[:1] == ("data",):
                        return str(message[1]).encode("utf-8")
                    if message[:1] == ("eof",):
                        return b""
                    raise RuntimeError(_broker_error_message(message))
                # Child exit is intentionally not EOF: the relay can still
                # have trailing terminal output to publish. Only an explicit
                # EOF message or broker exit closes the output stream.
                if handle.broker.exitcode is not None:
                    return b""
                await asyncio.sleep(0.01)
        while True:
            try:
                text = await asyncio.to_thread(handle.native_pty.read)
            except EOFError:
                return b""
            except (TimeoutError, socket.timeout):
                if handle.process.returncode is not None:
                    return b""
                continue
            if text:
                return text.encode("utf-8")
            if handle.process.returncode is not None:
                return b""

    async def resize(
        self, handle: WindowsPtyProcessHandle, cols: int, rows: int
    ) -> None:
        if cols <= 0 or rows <= 0:
            raise ValueError("terminal dimensions must be positive")
        if handle.broker is not None:
            await self._broker_request(handle, "setwinsize", rows, cols)
        else:
            await asyncio.to_thread(handle.native_pty.setwinsize, rows, cols)

    async def dispose(self, handle: ProcessHandle) -> None:
        pty_handle = _as_windows_pty(handle)
        if pty_handle._disposed:
            return
        if pty_handle._dispose_task is None:
            pty_handle._dispose_task = asyncio.create_task(
                self._dispose_owned_pty(pty_handle)
            )
            pty_handle._dispose_task.add_done_callback(_consume_future_outcome)
        await asyncio.shield(pty_handle._dispose_task)

    async def _dispose_owned_pty(self, pty_handle: WindowsPtyProcessHandle) -> None:
        if pty_handle._disposed:
            return
        pty_handle._closing = True
        cleanup_error: BaseException | None = None
        try:
            await self.kill_tree(pty_handle)
        except BaseException as err:  # noqa: BLE001 - cleanup must continue
            cleanup_error = err
        finally:
            # Releasing both kill-on-close handles is intentionally synchronous.
            # No await may intervene between a failed/slow empty check and this
            # final ownership boundary.
            for attribute in ("job", "broker_job"):
                job = getattr(pty_handle, attribute)
                if job is None:
                    continue
                try:
                    job.close()
                except BaseException as err:  # noqa: BLE001 - cleanup boundary
                    if cleanup_error is None:
                        cleanup_error = err
                finally:
                    setattr(pty_handle, attribute, None)
            pty_handle.output_closed = True
            pty_handle._tree_killed = True
            pty_handle._disposed = True

        if pty_handle.broker is not None:
            for connection in (
                pty_handle.broker_control,
                pty_handle.broker_output,
                getattr(pty_handle.process, "_status", None),
            ):
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
            reaper = _submit_daemon_native_call(_reap_broker_process, pty_handle.broker)
            try:
                await asyncio.shield(asyncio.wrap_future(reaper))
            except BaseException as err:  # noqa: BLE001 - cleanup boundary
                if cleanup_error is None:
                    cleanup_error = err
        elif pty_handle.native_pty is not None:
            try:
                if pty_handle.native_pty.isalive():
                    await asyncio.to_thread(pty_handle.native_pty.close, True)
            except BaseException as err:  # noqa: BLE001 - cleanup boundary
                if cleanup_error is None:
                    cleanup_error = err

        pending = pty_handle._write_future
        if pending is not None and not pending.done():
            try:
                if isinstance(pending, asyncio.Future):
                    await asyncio.wait_for(asyncio.shield(pending), 0.25)
                else:
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.wrap_future(pending)), 0.25
                    )
            except (asyncio.TimeoutError, OSError, RuntimeError):
                pass
        if cleanup_error is not None:
            raise cleanup_error

    async def _broker_request(
        self, handle: WindowsPtyProcessHandle, operation: str, *args: Any
    ) -> Any:
        if handle.broker_control is None or handle.broker is None:
            raise RuntimeError("ConPTY broker is unavailable")
        if handle._control_failed:
            raise RuntimeError("ConPTY broker control channel requires disposal")
        async with handle._control_lock:
            try:
                handle.broker_control.send((operation, args))
                response = await _receive_broker_message(
                    handle.broker_control, handle.broker
                )
            except (BrokenPipeError, EOFError, OSError):
                # The monitor can dispose the broker after observing child
                # exit while cancellation is between escalation steps. A
                # closed channel is success only for idempotent termination.
                if operation in {"sendintr", "terminate", "close"} and (
                    handle.process.returncode is not None or handle._tree_killed
                ):
                    return None
                handle._control_failed = True
                raise
            except BaseException:
                handle._control_failed = True
                raise
            if response[:1] != ("ok",):
                _raise_broker_error(response)
            return response[1] if len(response) > 1 else None


def _submit_daemon_native_call(
    operation: Callable[..., Any], *args: Any, **kwargs: Any
) -> Future[Any]:
    """Run a non-Windows test double without interpreter-owned executors."""

    future: Future[Any] = Future()

    def invoke() -> None:
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(operation(*args, **kwargs))
        except BaseException as err:  # noqa: BLE001 - native worker boundary
            future.set_exception(err)

    threading.Thread(target=invoke, name="sharkrail-conpty-test", daemon=True).start()
    return future


def _submit_conpty_start(
    operation: Callable[..., Any], *args: Any, **kwargs: Any
) -> Future[Any]:
    return _submit_daemon_native_call(operation, *args, **kwargs)


def _submit_conpty_write(
    operation: Callable[..., Any], *args: Any, **kwargs: Any
) -> Future[Any]:
    return _submit_daemon_native_call(operation, *args, **kwargs)


def _consume_future_outcome(completed: Any) -> None:
    if not completed.cancelled():
        completed.exception()


def _reap_broker_process(broker: Any) -> None:
    """Boundedly reap and close a multiprocessing broker in every exit state."""

    cleanup_error: BaseException | None = None
    try:
        try:
            broker.join(timeout=1.0)
        except (AssertionError, ValueError) as err:
            # A Process whose start failed has no child to reap, but still owns
            # a local Process object that must be closed below.
            cleanup_error = err
        try:
            alive = broker.is_alive()
        except (AssertionError, ValueError):
            alive = False
        if alive:
            broker.terminate()
            broker.join(timeout=1.0)
            alive = broker.is_alive()
        if alive:
            broker.kill()
            broker.join(timeout=1.0)
            alive = broker.is_alive()
        if alive:
            cleanup_error = TimeoutError("ConPTY broker did not exit after kill")
    finally:
        try:
            broker.close()
        except (AttributeError, ValueError) as err:
            if cleanup_error is None:
                cleanup_error = err
    if cleanup_error is not None and not isinstance(
        cleanup_error, (AssertionError, ValueError)
    ):
        raise cleanup_error


async def _receive_broker_message(connection: Any, broker: Any) -> tuple[Any, ...]:
    while not connection.poll():
        if broker.exitcode is not None:
            raise RuntimeError(
                f"ConPTY broker exited before replying (exit code {broker.exitcode})"
            )
        await asyncio.sleep(0.01)
    message = connection.recv()
    if not isinstance(message, tuple):
        raise TypeError("invalid response from ConPTY broker")
    return message


def _broker_error_message(message: tuple[Any, ...]) -> str:
    if len(message) >= 3 and message[0] == "error":
        return f"ConPTY broker {message[1]}: {message[2]}"
    return "invalid response from ConPTY broker"


def _raise_broker_error(message: tuple[Any, ...]) -> None:
    detail = _broker_error_message(message)
    if len(message) < 3 or message[0] != "error":
        raise RuntimeError(detail)
    error_type = str(message[1])
    native_errno = message[3] if len(message) > 3 else None
    errno_value = native_errno if isinstance(native_errno, int) else None
    native_winerror = message[4] if len(message) > 4 else None
    winerror_value = native_winerror if isinstance(native_winerror, int) else None
    error_args: tuple[Any, ...] = (errno_value, str(message[2]))
    if winerror_value is not None:
        error_args = (*error_args, None, winerror_value)
    if error_type == "FileNotFoundError":
        raise FileNotFoundError(*error_args)
    if error_type == "PermissionError":
        raise PermissionError(*error_args)
    if error_type == "OSError":
        raise OSError(*error_args)
    raise RuntimeError(detail)


def _resource_limiter(spec: CommandSpec) -> Optional[Callable[[], None]]:
    limits = spec.resources
    if (
        limits.memory_bytes is None
        and limits.cpu_time_seconds is None
        and limits.process_count is None
    ):
        return None

    def apply_limits() -> None:
        if limits.memory_bytes is not None:
            resource.setrlimit(
                resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes)
            )
        if limits.cpu_time_seconds is not None:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (limits.cpu_time_seconds, limits.cpu_time_seconds),
            )
        if limits.process_count is not None and hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(
                resource.RLIMIT_NPROC,
                (limits.process_count, limits.process_count),
            )

    return apply_limits


def _as_pty(handle: ProcessHandle) -> PtyProcessHandle:
    if not isinstance(handle, PtyProcessHandle):
        raise TypeError("PTY operation requires a PtyProcessHandle")
    return handle


def _as_windows_pty(handle: ProcessHandle) -> WindowsPtyProcessHandle:
    if not isinstance(handle, WindowsPtyProcessHandle):
        raise TypeError("ConPTY operation requires a WindowsPtyProcessHandle")
    return handle


async def _wait_for_fd(fd: int, *, writable: bool) -> None:
    """Wait for a non-blocking POSIX descriptor without occupying a worker thread."""

    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()

    def mark_ready() -> None:
        if not ready.done():
            ready.set_result(None)

    if writable:
        loop.add_writer(fd, mark_ready)
    else:
        loop.add_reader(fd, mark_ready)
    try:
        await ready
    finally:
        if writable:
            loop.remove_writer(fd)
        else:
            loop.remove_reader(fd)


async def _write_fd(fd: int, data: bytes) -> None:
    """Write all bytes to a non-blocking POSIX descriptor."""

    remaining = memoryview(data)
    while remaining:
        try:
            written = os.write(fd, remaining)
        except BlockingIOError:
            await _wait_for_fd(fd, writable=True)
            continue
        except InterruptedError:
            continue
        if written == 0:
            await _wait_for_fd(fd, writable=True)
            continue
        remaining = remaining[written:]


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
    step_handler: Optional[Callable[[CancellationStep], Awaitable[None]]] = None,
) -> tuple[CancellationStep, ...]:
    """Apply portable interrupt -> terminate -> kill-tree escalation."""
    policy = policy or CancellationPolicy()
    policy.validate()
    steps: list[CancellationStep] = []

    async def kill_tree() -> None:
        steps.append(CancellationStep.KILL_TREE)
        if step_handler is not None:
            await step_handler(CancellationStep.KILL_TREE)
        await backend.kill_tree(handle)

    if handle.process.returncode is not None:
        if handle.process_tree != "unknown":
            await kill_tree()
        return tuple(steps)

    if not policy.skip_interrupt:
        steps.append(CancellationStep.INTERRUPT)
        if step_handler is not None:
            await step_handler(CancellationStep.INTERRUPT)
        await backend.interrupt(handle)
        if await wait_for_exit(handle, policy.interrupt_grace_ms / 1000):
            # Waiting for the root does not prove that descendants in the
            # session-owned process tree exited with it.
            if handle.process_tree != "unknown":
                await kill_tree()
            return tuple(steps)

    steps.append(CancellationStep.TERMINATE)
    if step_handler is not None:
        await step_handler(CancellationStep.TERMINATE)
    await backend.terminate(handle)
    if await wait_for_exit(handle, policy.terminate_grace_ms / 1000):
        if handle.process_tree != "unknown":
            await kill_tree()
        return tuple(steps)

    await kill_tree()
    if not await wait_for_exit(handle, policy.kill_tree_grace_ms / 1000):
        raise TimeoutError("process tree did not exit after forced termination")
    return tuple(steps)
