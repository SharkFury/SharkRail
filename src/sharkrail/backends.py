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
from typing import Optional

from .models import CommandSpec


@dataclass
class ProcessHandle:
    process: asyncio.subprocess.Process
    stdin_closed: bool = False

    @property
    def pid(self) -> int:
        return self.process.pid


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


class PipeBackend(ExecutionBackend):
    """Pipe execution using a dedicated process group for tree operations."""

    async def start(self, spec: CommandSpec) -> ProcessHandle:
        kwargs: dict[str, object] = {
            "cwd": spec.cwd,
            "env": dict(spec.env) if spec.env is not None else None,
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
