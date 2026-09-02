"""Execution primitives for SharkRail."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional
from enum import Enum


from .models import CommandMode, CommandSpec


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
    max_output_bytes: int | None = None
    reason: CompletionReason = CompletionReason.SUCCESS
    timed_out: bool = False


class CommandRunner:
    def __init__(self, dry_run: bool = False, max_output_bytes: int | None = None) -> None:
        self._dry_run = dry_run
        self._max_output_bytes = max_output_bytes

    def _truncate_output(self, stdout: str, stderr: str) -> tuple[str, str, bool]:
        if self._max_output_bytes is None:
            return stdout, stderr, False
        if self._max_output_bytes <= 0:
            return "", "", True

        max_bytes = self._max_output_bytes
        if len(stdout) + len(stderr) <= max_bytes:
            return stdout, stderr, False

        if len(stdout) >= max_bytes:
            return stdout[:max_bytes], "", True

        truncated_stdout = stdout
        stderr_available = max_bytes - len(stdout)
        truncated_stderr = stderr[:stderr_available]
        return truncated_stdout, truncated_stderr, True

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
            result = CommandResult(
                exit_code=127,
                stdout="",
                stderr=stderr,
                max_output_bytes=self._max_output_bytes,
                reason=CompletionReason.FAILED,
                timed_out=False,
            )
            seq = await self._emit(event_handler, seq, LifecycleEventType.OUTPUT, {"stderr": stderr})
            seq = await self._emit(event_handler, seq, LifecycleEventType.EXITED, {"exit_code": result.exit_code})
            seq = await self._emit(event_handler, seq, LifecycleEventType.DRAINED, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
            seq = await self._emit(event_handler, seq, LifecycleEventType.COMPLETED, {"reason": result.reason.value, "timed_out": result.timed_out})
            return result, events
        except OSError as err:
            stderr = str(err)
            result = CommandResult(
                exit_code=1,
                stdout="",
                stderr=stderr,
                max_output_bytes=self._max_output_bytes,
                reason=CompletionReason.FAILED,
                timed_out=False,
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
            stdout_data = out.decode(errors="ignore")
            stderr_data = err.decode(errors="ignore")
            truncated_stdout, truncated_stderr, output_truncated = self._truncate_output(stdout_data, stderr_data)
            result = CommandResult(
                exit_code=124,
                stdout=truncated_stdout,
                stderr=truncated_stderr,
                output_truncated=output_truncated,
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

        stdout_data = out.decode(errors="ignore")
        stderr_data = err.decode(errors="ignore")
        truncated_stdout, truncated_stderr, output_truncated = self._truncate_output(stdout_data, stderr_data)

        result = CommandResult(
            exit_code=proc.returncode if proc.returncode is not None else 1,
            stdout=truncated_stdout,
            stderr=truncated_stderr,
            output_truncated=output_truncated,
            max_output_bytes=self._max_output_bytes,
            reason=reason,
            timed_out=False,
        )

        seq = await self._emit(event_handler, seq, LifecycleEventType.OUTPUT, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
        seq = await self._emit(event_handler, seq, LifecycleEventType.EXITED, {"exit_code": result.exit_code})
        seq = await self._emit(event_handler, seq, LifecycleEventType.DRAINED, {"stdout_len": len(result.stdout), "stderr_len": len(result.stderr)})
        seq = await self._emit(event_handler, seq, LifecycleEventType.COMPLETED, {"reason": result.reason.value, "timed_out": result.timed_out})

        return result, events
