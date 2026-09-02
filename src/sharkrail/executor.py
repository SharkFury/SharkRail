"""Execution primitives for SharkRail."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from .errors import ErrorCode, ErrorStage, ExecutionError
from .models import CommandMode, CommandSpec
from .output import capture_output


class CompletionReason(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILED = "failed"


class LifecycleEventType(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    OUTPUT = "output"
    EXITED = "exited"
    DRAINED = "drained"
    COMPLETED = "completed"


@dataclass(frozen=True)
class LifecycleEvent:
    seq: int
    kind: LifecycleEventType
    payload: dict[str, str | int | bool]


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
    def __init__(self, dry_run: bool = False, max_output_bytes: int | None = None) -> None:
        self._dry_run = dry_run
        self._max_output_bytes = max_output_bytes

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

        # PTY support is part of the roadmap. Foundation layer keeps a single,
        # portable execution path while exposing explicit mode for callers.
        if spec.mode == CommandMode.PTY:
            # Keep PTY explicit for compatibility. At this stage we run with
            # normal pipes and clearly expose the mode in command handling.
            pass

        try:
            proc = await asyncio.create_subprocess_exec(
                *spec.argv_list,
                cwd=spec.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
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
            if timeout_ms is None:
                out, err = await proc.communicate()
            else:
                out, err = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_ms / 1000,
                )
        except asyncio.TimeoutError:
            proc.kill()
            out, err = await proc.communicate()
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

        if proc.returncode == 0:
            reason = CompletionReason.SUCCESS
        elif proc.returncode is None:
            reason = CompletionReason.FAILED
        else:
            reason = CompletionReason.FAILED

        captured = capture_output(out, err, self._max_output_bytes)

        result = CommandResult(
            exit_code=proc.returncode if proc.returncode is not None else 1,
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

        return result, events
