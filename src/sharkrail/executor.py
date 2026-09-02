"""Execution primitives for SharkRail."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timezone
from enum import Enum
from time import monotonic_ns
from typing import Callable, Optional

from .backends import ExecutionBackend
from .errors import ErrorCode, ExecutionError, SharkRailError
from .models import CommandSpec


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
    RESOURCE_LIMIT_HIT = "resource.limit_hit"


@dataclass(frozen=True)
class LifecycleEvent:
    seq: int
    kind: LifecycleEventType
    payload: dict[str, object]
    timestamp: str = dataclass_field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    monotonic_ns: int = dataclass_field(default_factory=monotonic_ns)
    trace_id: Optional[str] = None


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
    duration_ms: float = 0.0
    drain_duration_ms: float = 0.0


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
        if self._dry_run:
            result = CommandResult(
                exit_code=0,
                stdout="",
                stderr="",
                output_truncated=False,
                max_output_bytes=self._max_output_bytes,
            )
            dry_run_events = [
                LifecycleEvent(
                    seq=0,
                    kind=LifecycleEventType.ACCEPTED,
                    payload={"executable": spec.executable, "dry_run": True},
                ),
                LifecycleEvent(
                    seq=1,
                    kind=LifecycleEventType.SESSION_COMPLETED,
                    payload={"exit_code": 0, "reason": CompletionReason.SUCCESS.value},
                ),
            ]
            if event_handler is not None:
                for event in dry_run_events:
                    event_handler(event)
            return result, dry_run_events

        # Imported lazily because sessions owns the execution engine while its
        # public result and event contracts are defined in this module.
        from .sessions import SessionManager

        manager = SessionManager(backend=self._backend)

        try:
            session = await manager.start(
                spec,
                timeout_ms=timeout_ms,
                max_output_bytes=self._max_output_bytes,
            )
        except SharkRailError as err:
            execution_error = err.error
            exit_code = 127 if execution_error.code == ErrorCode.EXECUTABLE_NOT_FOUND else 1
            result = CommandResult(
                exit_code=exit_code,
                stdout="",
                stderr=execution_error.message,
                max_output_bytes=self._max_output_bytes,
                reason=CompletionReason.FAILED,
                error=execution_error,
            )
            events = [
                LifecycleEvent(0, LifecycleEventType.ACCEPTED, {"executable": spec.executable}),
                LifecycleEvent(1, LifecycleEventType.SESSION_ERROR, execution_error.to_dict()),
                LifecycleEvent(
                    2,
                    LifecycleEventType.SESSION_COMPLETED,
                    {"reason": CompletionReason.FAILED.value, "exit_code": exit_code},
                ),
            ]
        else:
            waited = await manager.wait(session.id)
            if waited is None:  # pragma: no cover - an unbounded wait always completes
                raise RuntimeError("session completed without a result")
            result = waited
            events = list(session.events)
            await manager.dispose(session.id)

        if event_handler is not None:
            for event in events:
                event_handler(event)
        return result, events
