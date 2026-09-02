"""Execution primitives for SharkRail."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .backends import (
    ExecutionBackend,
    PtyProcessHandle,
    pipe_backend,
    pty_backend,
    read_pty_output,
)
from .errors import ErrorCode, ErrorStage, ExecutionError
from .models import CommandMode, CommandSpec
from .output import capture_output


class CompletionReason(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILED = "failed"
    CANCELLED = "cancelled"
    KILLED = "killed"
    RESOURCE_LIMITED = "resource_limited"


class LifecycleEventType(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    OUTPUT = "output"
    EXITED = "exited"
    DRAINED = "drained"
    COMPLETED = "completed"
    PROCESS_STARTED = "process.started"
    STDOUT = "stdout"
    STDERR = "stderr"
    PTY_OUTPUT = "pty.output"
    OUTPUT_TRUNCATED = "output.truncated"
    PROCESS_EXITED = "process.exited"
    SESSION_DRAINED = "session.drained"
    SESSION_COMPLETED = "session.completed"
    SESSION_ERROR = "session.error"
    CANCELLATION_STEP = "cancellation.step"


@dataclass(frozen=True)
class LifecycleEvent:
    seq: int
    kind: LifecycleEventType
    payload: dict[str, object]


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    output_truncated: bool = False
    retained_output_bytes: int = 0
    truncated_output_bytes: int = 0
    decoding_errors: bool = False
    max_output_bytes: int | None = None
    reason: CompletionReason = CompletionReason.SUCCESS
    timed_out: bool = False
    error: ExecutionError | None = None


class CommandRunner:
    def __init__(
        self,
        dry_run: bool = False,
        max_output_bytes: int | None = None,
        backend: ExecutionBackend | None = None,
    ) -> None:
        self._dry_run = dry_run
        self._max_output_bytes = max_output_bytes
        self._backend = backend

    async def _emit(
        self,
        handler: Optional[Callable[[LifecycleEvent], None]],
        seq: int,
        kind: LifecycleEventType,
        payload: Optional[dict[str, str | int | bool]] = None,
    ) -> int:
        if handler is None:
            return seq + 1
        event = LifecycleEvent(seq=seq, kind=kind, payload=payload or {})
        handler(event)
        return seq + 1

    async def run(
        self,
        spec: CommandSpec,
        timeout_ms: Optional[int] = None,
    ) -> CommandResult:
        result, _ = await self.run_events(spec, timeout_ms=timeout_ms)
        return result

    async def run_events(
        self,
        spec: CommandSpec,
        timeout_ms: Optional[int] = None,
        event_handler: Optional[Callable[[LifecycleEvent], None]] = None,
    ) -> tuple[CommandResult, list[LifecycleEvent]]:
        spec.validate()

        events: list[LifecycleEvent] = []
        if event_handler is None:
            event_handler = events.append
        seq = 0
        seq = await self._emit(event_handler, seq, LifecycleEventType.ACCEPTED, {"executable": spec.executable})

        if self._dry_run:
            seq = await self._emit(event_handler, seq, LifecycleEventType.RUNNING, {"dry_run": True})
            seq = await self._emit(event_handler, seq, LifecycleEventType.COMPLETED, {"exit_code": 0, "reason": CompletionReason.SUCCESS})
            return CommandResult(
                exit_code=0,
                stdout="",
                stderr="",
                output_truncated=False,
                max_output_bytes=self._max_output_bytes,
            ), events

        seq = await self._emit(event_handler, seq, LifecycleEventType.RUNNING, {"mode": spec.mode.value})

        backend = self._backend or (pty_backend() if spec.mode == CommandMode.PTY else pipe_backend())

        try:
            handle = await backend.start(spec)
        except FileNotFoundError as err:
            stderr = str(err)
            execution_error = ExecutionError(
                code=ErrorCode.EXECUTABLE_NOT_FOUND,
                stage=ErrorStage.START,
                message=stderr,
                native={"errno": err.errno} if err.errno is not None else {},
            )
            result = CommandResult(
                exit_code=127,
                stdout="",
                stderr=stderr,
                max_output_bytes=self._max_output_bytes,
                reason=CompletionReason.FAILED,
                timed_out=False,
                error=execution_error,
            )
            seq = await self._emit(event_handler, seq, LifecycleEventType.OUTPUT, {"stderr": stderr})
            seq = await self._emit(event_handler, seq, LifecycleEventType.EXITED, {"exit_code": result.exit_code})
            seq = await self._emit(event_handler, seq, LifecycleEventType.DRAINED, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
            seq = await self._emit(event_handler, seq, LifecycleEventType.COMPLETED, {"reason": result.reason.value, "timed_out": result.timed_out})
            return result, events
        except OSError as err:
            stderr = str(err)
            execution_error = ExecutionError(
                code=ErrorCode.START_FAILED,
                stage=ErrorStage.START,
                message=stderr,
                native={"errno": err.errno} if err.errno is not None else {},
            )
            result = CommandResult(
                exit_code=1,
                stdout="",
                stderr=stderr,
                max_output_bytes=self._max_output_bytes,
                reason=CompletionReason.FAILED,
                timed_out=False,
                error=execution_error,
            )
            seq = await self._emit(event_handler, seq, LifecycleEventType.OUTPUT, {"stderr": stderr})
            seq = await self._emit(event_handler, seq, LifecycleEventType.EXITED, {"exit_code": result.exit_code})
            seq = await self._emit(event_handler, seq, LifecycleEventType.DRAINED, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
            seq = await self._emit(event_handler, seq, LifecycleEventType.COMPLETED, {"reason": result.reason.value, "timed_out": result.timed_out})
            return result, events

        try:
            if isinstance(handle, PtyProcessHandle):
                output_task = asyncio.create_task(read_pty_output(backend, handle))
                if timeout_ms is None:
                    await handle.process.wait()
                else:
                    await asyncio.wait_for(handle.process.wait(), timeout=timeout_ms / 1000)
                out, err = await output_task, b""
                await backend.dispose(handle)
            elif timeout_ms is None:
                out, err = await handle.process.communicate()
            else:
                out, err = await asyncio.wait_for(
                    handle.process.communicate(),
                    timeout=timeout_ms / 1000,
                )
        except asyncio.TimeoutError:
            await backend.kill_tree(handle)
            if isinstance(handle, PtyProcessHandle):
                await handle.process.wait()
                out, err = await output_task, b""
            else:
                out, err = await handle.process.communicate()
            await backend.dispose(handle)
            captured = capture_output(out, err, self._max_output_bytes)
            result = CommandResult(
                exit_code=124,
                stdout=captured.stdout,
                stderr=captured.stderr,
                output_truncated=captured.truncated,
                retained_output_bytes=captured.retained_bytes,
                truncated_output_bytes=captured.truncated_bytes,
                decoding_errors=captured.decoding_errors,
                max_output_bytes=self._max_output_bytes,
                reason=CompletionReason.TIMEOUT,
                timed_out=True,
            )
            seq = await self._emit(event_handler, seq, LifecycleEventType.OUTPUT, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
            seq = await self._emit(event_handler, seq, LifecycleEventType.EXITED, {"exit_code": result.exit_code})
            seq = await self._emit(event_handler, seq, LifecycleEventType.DRAINED, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
            seq = await self._emit(event_handler, seq, LifecycleEventType.COMPLETED, {"reason": result.reason.value, "timed_out": True})
            return result, events

        if handle.process.returncode == 0:
            reason = CompletionReason.SUCCESS
        elif handle.process.returncode is None:
            reason = CompletionReason.FAILED
        else:
            reason = CompletionReason.FAILED

        captured = capture_output(out, err, self._max_output_bytes)

        result = CommandResult(
            exit_code=handle.process.returncode if handle.process.returncode is not None else 1,
            stdout=captured.stdout,
            stderr=captured.stderr,
            output_truncated=captured.truncated,
            retained_output_bytes=captured.retained_bytes,
            truncated_output_bytes=captured.truncated_bytes,
            decoding_errors=captured.decoding_errors,
            max_output_bytes=self._max_output_bytes,
            reason=reason,
            timed_out=False,
        )

        seq = await self._emit(event_handler, seq, LifecycleEventType.OUTPUT, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
        seq = await self._emit(event_handler, seq, LifecycleEventType.EXITED, {"exit_code": result.exit_code})
        seq = await self._emit(event_handler, seq, LifecycleEventType.DRAINED, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
        seq = await self._emit(event_handler, seq, LifecycleEventType.COMPLETED, {"reason": result.reason.value, "timed_out": result.timed_out})

        await backend.dispose(handle)

        return result, events
