"""Concurrent execution session management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from uuid import uuid4

from .backends import (
    CancellationPolicy,
    ExecutionBackend,
    ProcessHandle,
    PtyProcessHandle,
    cancel_process,
    pipe_backend,
    pty_backend,
)
from .errors import ErrorCode, ErrorStage, ExecutionError, SharkRailError
from .executor import (
    CommandResult,
    CompletionReason,
    LifecycleEvent,
    LifecycleEventType,
)
from .models import CommandMode, CommandSpec
from .output import capture_output


class SessionState(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    CANCELLING = "cancelling"
    DRAINING = "draining"
    COMPLETED = "completed"
    DISPOSED = "disposed"


@dataclass
class Session:
    id: str
    spec: CommandSpec
    backend: ExecutionBackend
    handle: ProcessHandle
    max_output_bytes: Optional[int]
    timeout_ms: Optional[int]
    state: SessionState = SessionState.ACCEPTED
    events: list[LifecycleEvent] = field(default_factory=list)
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    total_output_bytes: int = 0
    truncated_output_bytes: int = 0
    stream_offsets: dict[str, int] = field(default_factory=dict)
    result: Optional[CommandResult] = None
    completion_reason: Optional[CompletionReason] = None
    max_output_events: int = 10000
    output_event_count: int = 0
    output_event_limit_reported: bool = False
    monitor_task: Optional[asyncio.Task[None]] = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def emit(self, kind: LifecycleEventType, payload: Optional[dict[str, object]] = None) -> None:
        async with self.condition:
            self.events.append(
                LifecycleEvent(seq=len(self.events), kind=kind, payload=payload or {})
            )
            self.condition.notify_all()

    async def append_output(self, stream: str, data: bytes) -> None:
        offset = self.stream_offsets.get(stream, 0)
        self.stream_offsets[stream] = offset + len(data)
        self.total_output_bytes += len(data)

        remaining = len(data)
        if self.max_output_bytes is not None:
            retained = len(self.stdout) + len(self.stderr)
            remaining = max(0, self.max_output_bytes - retained)
        kept = data[:remaining]
        dropped = len(data) - len(kept)
        if stream == "stderr":
            self.stderr.extend(kept)
            kind = LifecycleEventType.STDERR
        elif stream == "pty":
            self.stdout.extend(kept)
            kind = LifecycleEventType.PTY_OUTPUT
        else:
            self.stdout.extend(kept)
            kind = LifecycleEventType.STDOUT

        if kept and self.output_event_count < self.max_output_events:
            text = kept.decode("utf-8", errors="replace")
            await self.emit(
                kind,
                {
                    "stream": stream,
                    "offset": offset,
                    "bytes": len(kept),
                    "encoding": "utf-8",
                    "text": text,
                    "decoding_errors": "\ufffd" in text,
                },
            )
            self.output_event_count += 1
        elif kept and not self.output_event_limit_reported:
            self.output_event_limit_reported = True
            await self.emit(
                LifecycleEventType.RESOURCE_LIMIT_HIT,
                {"resource": "output_events", "limit": self.max_output_events},
            )
        if dropped:
            self.truncated_output_bytes += dropped
            await self.emit(
                LifecycleEventType.OUTPUT_TRUNCATED,
                {"stream": stream, "dropped_bytes": dropped, "total_dropped_bytes": self.truncated_output_bytes},
            )


class SessionManager:
    def __init__(
        self,
        default_max_output_bytes: int = 16 * 1024 * 1024,
        max_active_sessions: int = 64,
        max_input_bytes: int = 1024 * 1024,
        max_output_events: int = 10000,
    ) -> None:
        if default_max_output_bytes < 0:
            raise ValueError("default_max_output_bytes must be non-negative")
        if max_active_sessions <= 0 or max_input_bytes <= 0 or max_output_events <= 0:
            raise ValueError("session resource limits must be positive")
        self._default_max_output_bytes = default_max_output_bytes
        self._max_active_sessions = max_active_sessions
        self._max_input_bytes = max_input_bytes
        self._max_output_events = max_output_events
        self._sessions: dict[str, Session] = {}

    async def start(
        self,
        spec: CommandSpec,
        *,
        timeout_ms: Optional[int] = None,
        max_output_bytes: Optional[int] = None,
    ) -> Session:
        spec.validate()
        active_count = sum(
            session.state not in {SessionState.COMPLETED, SessionState.DISPOSED}
            for session in self._sessions.values()
        )
        if active_count >= self._max_active_sessions:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.RESOURCE_LIMITED,
                    stage=ErrorStage.START,
                    message=f"active session limit reached ({self._max_active_sessions})",
                    retryable=True,
                )
            )
        if timeout_ms is not None and timeout_ms < 0:
            raise self._request_error("timeout_ms must be non-negative")
        if max_output_bytes is not None and max_output_bytes < 0:
            raise self._request_error("max_output_bytes must be non-negative")
        backend: ExecutionBackend = pty_backend() if spec.mode == CommandMode.PTY else pipe_backend()
        try:
            handle = await backend.start(spec)
        except FileNotFoundError as err:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.EXECUTABLE_NOT_FOUND,
                    stage=ErrorStage.START,
                    message=str(err),
                    native={"errno": err.errno} if err.errno is not None else {},
                ),
                err,
            ) from err
        except OSError as err:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.START_FAILED,
                    stage=ErrorStage.START,
                    message=str(err),
                    native={"errno": err.errno} if err.errno is not None else {},
                ),
                err,
            ) from err

        session = Session(
            id=str(uuid4()),
            spec=spec,
            backend=backend,
            handle=handle,
            max_output_bytes=self._default_max_output_bytes if max_output_bytes is None else max_output_bytes,
            timeout_ms=timeout_ms,
            max_output_events=self._max_output_events,
        )
        self._sessions[session.id] = session
        await session.emit(LifecycleEventType.ACCEPTED, {"session_id": session.id})
        session.state = SessionState.RUNNING
        await session.emit(
            LifecycleEventType.PROCESS_STARTED,
            {"pid": handle.pid, "mode": spec.mode.value},
        )
        session.monitor_task = asyncio.create_task(self._monitor(session))
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as err:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.SESSION_NOT_FOUND,
                    stage=ErrorStage.RUN,
                    message=f"Session not found: {session_id}",
                ),
                err,
            ) from err

    async def write(self, session_id: str, data: bytes) -> None:
        session = self._require_active(session_id)
        if len(data) > self._max_input_bytes:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.RESOURCE_LIMITED,
                    stage=ErrorStage.RUN,
                    message=f"input exceeds per-write limit ({self._max_input_bytes} bytes)",
                )
            )
        await session.backend.write(session.handle, data)

    async def close_stdin(self, session_id: str) -> None:
        session = self._require_active(session_id)
        await session.backend.close_stdin(session.handle)

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._require_active(session_id)
        if not hasattr(session.backend, "resize") or not isinstance(session.handle, PtyProcessHandle):
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.CAPABILITY_NOT_SUPPORTED,
                    stage=ErrorStage.RUN,
                    message="resize is only supported for PTY sessions",
                )
            )
        await session.backend.resize(session.handle, cols, rows)

    async def interrupt(self, session_id: str) -> None:
        session = self._require_active(session_id)
        await session.emit(LifecycleEventType.CANCELLATION_STEP, {"step": "interrupt"})
        await session.backend.interrupt(session.handle)

    async def cancel(
        self,
        session_id: str,
        policy: Optional[CancellationPolicy] = None,
    ) -> tuple[str, ...]:
        session = self._require_active(session_id)
        session.state = SessionState.CANCELLING
        session.completion_reason = CompletionReason.CANCELLED
        steps = await cancel_process(session.backend, session.handle, policy)
        for step in steps:
            await session.emit(LifecycleEventType.CANCELLATION_STEP, {"step": step.value})
        return tuple(step.value for step in steps)

    async def wait(self, session_id: str, timeout_ms: Optional[int] = None) -> Optional[CommandResult]:
        session = self.get(session_id)
        if session.monitor_task is None:
            return session.result
        try:
            if timeout_ms is None:
                await asyncio.shield(session.monitor_task)
            else:
                await asyncio.wait_for(asyncio.shield(session.monitor_task), timeout_ms / 1000)
        except asyncio.TimeoutError:
            return None
        return session.result

    async def events_after(
        self,
        session_id: str,
        cursor: int = 0,
        wait_ms: int = 0,
    ) -> tuple[LifecycleEvent, ...]:
        session = self.get(session_id)
        if cursor < 0 or cursor > len(session.events):
            raise self._request_error("event cursor is out of range")
        if cursor == len(session.events) and wait_ms > 0 and session.state != SessionState.COMPLETED:
            async with session.condition:
                try:
                    await asyncio.wait_for(session.condition.wait(), wait_ms / 1000)
                except asyncio.TimeoutError:
                    pass
        return tuple(session.events[cursor:])

    async def dispose(self, session_id: str) -> None:
        session = self.get(session_id)
        if session.state not in {SessionState.COMPLETED, SessionState.DISPOSED}:
            await self.cancel(session_id, CancellationPolicy(skip_interrupt=True))
            await self.wait(session_id)
        await session.backend.dispose(session.handle)
        session.state = SessionState.DISPOSED
        self._sessions.pop(session_id, None)

    async def _monitor(self, session: Session) -> None:
        readers: list[asyncio.Task[None]] = []
        if hasattr(session.backend, "read") and isinstance(session.handle, PtyProcessHandle):
            readers.append(asyncio.create_task(self._read_pty(session, session.backend, session.handle)))
        else:
            if session.handle.process.stdout is not None:
                readers.append(asyncio.create_task(self._read_pipe(session, "stdout", session.handle.process.stdout)))
            if session.handle.process.stderr is not None:
                readers.append(asyncio.create_task(self._read_pipe(session, "stderr", session.handle.process.stderr)))

        try:
            if session.timeout_ms is None:
                await session.handle.process.wait()
            else:
                await asyncio.wait_for(session.handle.process.wait(), session.timeout_ms / 1000)
        except asyncio.TimeoutError:
            session.completion_reason = CompletionReason.TIMEOUT
            await session.backend.kill_tree(session.handle)
            await session.handle.process.wait()

        session.state = SessionState.DRAINING
        await session.emit(
            LifecycleEventType.PROCESS_EXITED,
            {"exit_code": session.handle.process.returncode},
        )
        await asyncio.gather(*readers)
        await session.emit(
            LifecycleEventType.SESSION_DRAINED,
            {"output_bytes": session.total_output_bytes},
        )
        await session.backend.dispose(session.handle)

        reason = session.completion_reason
        if reason is None:
            reason = CompletionReason.SUCCESS if session.handle.process.returncode == 0 else CompletionReason.FAILED
        captured = capture_output(bytes(session.stdout), bytes(session.stderr), None)
        exit_code = session.handle.process.returncode
        if reason == CompletionReason.TIMEOUT:
            exit_code = 124
        session.result = CommandResult(
            exit_code=exit_code if exit_code is not None else 1,
            stdout=captured.stdout,
            stderr=captured.stderr,
            output_truncated=session.truncated_output_bytes > 0,
            retained_output_bytes=captured.retained_bytes,
            truncated_output_bytes=session.truncated_output_bytes,
            decoding_errors=captured.decoding_errors,
            max_output_bytes=session.max_output_bytes,
            reason=reason,
            timed_out=reason == CompletionReason.TIMEOUT,
        )
        session.state = SessionState.COMPLETED
        await session.emit(
            LifecycleEventType.SESSION_COMPLETED,
            {"reason": reason.value, "exit_code": session.result.exit_code},
        )

    async def _read_pipe(
        self,
        session: Session,
        stream: str,
        reader: asyncio.StreamReader,
    ) -> None:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            await session.append_output(stream, chunk)

    async def _read_pty(
        self,
        session: Session,
        backend: object,
        handle: PtyProcessHandle,
    ) -> None:
        while True:
            chunk = await backend.read(handle)
            if not chunk:
                return
            await session.append_output("pty", chunk)

    def _require_active(self, session_id: str) -> Session:
        session = self.get(session_id)
        if session.state not in {SessionState.RUNNING, SessionState.CANCELLING}:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.INVALID_SESSION_STATE,
                    stage=ErrorStage.RUN,
                    message=f"Session {session_id} is {session.state.value}",
                )
            )
        return session

    @staticmethod
    def _request_error(message: str) -> SharkRailError:
        return SharkRailError(
            ExecutionError(
                code=ErrorCode.INVALID_REQUEST,
                stage=ErrorStage.VALIDATE,
                message=message,
            )
        )
