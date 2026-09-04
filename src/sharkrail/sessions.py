"""Concurrent execution session management."""

from __future__ import annotations

import asyncio
import base64
import codecs
import json
import logging
import signal
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from .backends import (
    CancellationPolicy,
    CancellationStep,
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
from .lifecycle import SessionLifecycle, SessionState
from .models import CommandMode, CommandSpec
from .output import capture_output
from .policy import ExecutionPolicy, PolicyViolation
from .telemetry import EventRecorder, log_event, observe_session


@dataclass
class Session:
    id: str
    spec: CommandSpec
    backend: ExecutionBackend
    handle: ProcessHandle
    max_output_bytes: Optional[int]
    timeout_ms: Optional[int]
    idle_timeout_ms: Optional[int] = None
    lifecycle: SessionLifecycle = field(default_factory=SessionLifecycle)
    events: deque[LifecycleEvent] = field(default_factory=deque)
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    total_output_bytes: int = 0
    truncated_output_bytes: int = 0
    stream_offsets: dict[str, int] = field(default_factory=dict)
    result: Optional[CommandResult] = None
    completion_reason: Optional[CompletionReason] = None
    max_output_events: int = 10000
    max_retained_events: int = 12000
    output_event_count: int = 0
    output_event_limit_reported: bool = False
    truncation_reported_streams: set[str] = field(default_factory=set)
    next_event_seq: int = 0
    completed_at_monotonic: Optional[float] = None
    monitor_task: Optional[asyncio.Task[None]] = None
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancellation_steps: tuple[str, ...] = ()
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    request_id: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    created_monotonic: float = field(default_factory=time.monotonic)
    started_monotonic: float = field(default_factory=time.monotonic)
    exited_monotonic: Optional[float] = None
    drain_started_monotonic: Optional[float] = None
    last_output_monotonic: Optional[float] = None
    input_bytes: int = 0
    stream_decoders: dict[str, Any] = field(default_factory=dict)
    event_recorder: Optional[EventRecorder] = None
    output_retention: Literal["head", "tail"] = "head"

    @property
    def state(self) -> SessionState:
        return self.lifecycle.state

    def transition(self, target: SessionState) -> None:
        self.lifecycle.transition(target)

    async def emit(
        self, kind: LifecycleEventType, payload: Optional[dict[str, object]] = None
    ) -> None:
        async with self.condition:
            if len(self.events) >= self.max_retained_events:
                self.events.popleft()
            event = LifecycleEvent(
                self.next_event_seq,
                kind,
                payload or {},
                trace_id=self.trace_id,
            )
            self.events.append(event)
            self.next_event_seq += 1
            self.condition.notify_all()
        if self.event_recorder is not None:
            await asyncio.to_thread(
                self.event_recorder.record,
                session_id=self.id,
                trace_id=self.trace_id,
                seq=event.seq,
                kind=event.kind.value,
                timestamp=event.timestamp,
                payload=event.payload,
            )

    @property
    def first_event_seq(self) -> int:
        return self.events[0].seq if self.events else self.next_event_seq

    async def append_output(self, stream: str, data: bytes) -> None:
        self.last_output_monotonic = time.monotonic()
        offset = self.stream_offsets.get(stream, 0)
        self.stream_offsets[stream] = offset + len(data)
        self.total_output_bytes += len(data)

        remaining = len(data)
        if self.max_output_bytes is not None:
            retained = len(self.stdout) + len(self.stderr)
            remaining = max(0, self.max_output_bytes - retained)
        kept = data[:remaining]
        dropped = len(data) - len(kept)
        if self.output_retention == "tail":
            kept = data
            dropped = 0
        if stream == "stderr":
            self.stderr.extend(kept)
            kind = LifecycleEventType.STDERR
        elif stream == "pty":
            self.stdout.extend(kept)
            kind = LifecycleEventType.PTY_OUTPUT
        else:
            self.stdout.extend(kept)
            kind = LifecycleEventType.STDOUT

        if self.output_retention == "tail" and self.max_output_bytes is not None:
            overflow = max(
                0, len(self.stdout) + len(self.stderr) - self.max_output_bytes
            )
            if overflow:
                del self.stdout[:overflow]
                dropped = overflow
            if len(kept) > self.max_output_bytes:
                kept = kept[-self.max_output_bytes :] if self.max_output_bytes else b""
                offset += len(data) - len(kept)

        if kept and self.output_event_count < self.max_output_events:
            decoder = self.stream_decoders.get(stream)
            if decoder is None:
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                self.stream_decoders[stream] = decoder
            text = decoder.decode(kept, final=False)
            await self.emit(
                kind,
                {
                    "stream": stream,
                    "offset": offset,
                    "bytes": len(kept),
                    "encoding": "utf-8",
                    "text": text,
                    "data_base64": base64.b64encode(kept).decode("ascii"),
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
            if stream not in self.truncation_reported_streams:
                self.truncation_reported_streams.add(stream)
                await self.emit(
                    LifecycleEventType.OUTPUT_TRUNCATED,
                    {
                        "stream": stream,
                        "dropped_bytes": dropped,
                        "total_dropped_bytes": self.truncated_output_bytes,
                    },
                )


class SessionManager:
    def __init__(
        self,
        default_max_output_bytes: int = 16 * 1024 * 1024,
        max_active_sessions: int = 64,
        max_input_bytes: int = 1024 * 1024,
        max_total_input_bytes: int = 16 * 1024 * 1024,
        max_output_events: int = 10000,
        backend: Optional[ExecutionBackend] = None,
        drain_timeout_ms: int = 2000,
        termination_timeout_ms: int = 2000,
        shutdown_timeout_ms: int = 5000,
        max_retained_events: int = 12000,
        max_completed_sessions: int = 256,
        completed_session_ttl_ms: int = 5 * 60 * 1000,
        max_event_page_size: int = 100,
        max_event_page_bytes: int = 256 * 1024,
        event_recorder: Optional[EventRecorder] = None,
        policy: ExecutionPolicy | None = None,
    ) -> None:
        if default_max_output_bytes < 0:
            raise ValueError("default_max_output_bytes must be non-negative")
        if (
            max_active_sessions <= 0
            or max_input_bytes <= 0
            or max_total_input_bytes <= 0
            or max_output_events <= 0
            or drain_timeout_ms < 0
            or termination_timeout_ms <= 0
            or shutdown_timeout_ms <= 0
            or max_retained_events <= 0
            or max_completed_sessions <= 0
            or completed_session_ttl_ms < 0
            or max_event_page_size <= 0
            or max_event_page_bytes <= 0
        ):
            raise ValueError("session resource limits must be positive")
        self._default_max_output_bytes = default_max_output_bytes
        self._max_active_sessions = max_active_sessions
        self._max_input_bytes = max_input_bytes
        self._max_total_input_bytes = max_total_input_bytes
        self._max_output_events = max_output_events
        self._backend = backend
        self._drain_timeout_ms = drain_timeout_ms
        self._termination_timeout_ms = termination_timeout_ms
        self._shutdown_timeout_ms = shutdown_timeout_ms
        self._max_retained_events = max_retained_events
        self._max_completed_sessions = max_completed_sessions
        self._completed_session_ttl_ms = completed_session_ttl_ms
        self._max_event_page_size = max_event_page_size
        self._max_event_page_bytes = max_event_page_bytes
        self._event_recorder = event_recorder
        self._policy = policy
        self._sessions: dict[str, Session] = {}
        self._disposed_session_ids: deque[str] = deque(maxlen=1024)
        self._expired_session_ids: deque[str] = deque(maxlen=1024)
        self._started_sessions = 0
        self._completion_counts: Counter[str] = Counter()
        self._error_counts: Counter[str] = Counter()
        self._total_input_bytes = 0
        self._total_output_bytes = 0
        self._total_retained_output_bytes = 0
        self._total_dropped_output_bytes = 0
        self._cancellation_count = 0
        self._created_monotonic = time.monotonic()
        self._starting_sessions = 0

    async def start(
        self,
        spec: CommandSpec,
        *,
        timeout_ms: Optional[int] = None,
        idle_timeout_ms: Optional[int] = None,
        max_output_bytes: Optional[int] = None,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
        output_retention: Literal["head", "tail"] = "head",
    ) -> Session:
        self._prune_completed_sessions()
        requested_monotonic = time.monotonic()
        spec.validate()
        if self._policy is not None:
            try:
                self._policy.enforce(
                    spec,
                    timeout_ms=timeout_ms,
                    max_output_bytes=(
                        self._default_max_output_bytes
                        if max_output_bytes is None
                        else max_output_bytes
                    ),
                )
            except PolicyViolation as err:
                raise SharkRailError(
                    ExecutionError(
                        code=ErrorCode.POLICY_DENIED,
                        stage=ErrorStage.VALIDATE,
                        message=str(err),
                        native={"rule": err.rule},
                    ),
                    err,
                ) from err
        if timeout_ms is not None and timeout_ms < 0:
            raise self._request_error("timeout_ms must be non-negative")
        if idle_timeout_ms is not None and idle_timeout_ms <= 0:
            raise self._request_error("idle_timeout_ms must be positive")
        if max_output_bytes is not None and max_output_bytes < 0:
            raise self._request_error("max_output_bytes must be non-negative")
        if output_retention not in {"head", "tail"}:
            raise self._request_error("output_retention must be head or tail")
        if output_retention == "tail" and spec.mode != CommandMode.PTY:
            raise self._request_error("tail output retention requires PTY mode")
        # No await is allowed between the count and reservation. Cooperative
        # asyncio scheduling therefore makes admission atomic within the
        # manager's owning event loop without serializing process creation.
        active_count = sum(
            session.state
            not in {
                SessionState.COMPLETED,
                SessionState.FAILED,
                SessionState.DISPOSED,
            }
            for session in self._sessions.values()
        )
        if active_count + self._starting_sessions >= self._max_active_sessions:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.RESOURCE_LIMITED,
                    stage=ErrorStage.START,
                    message=(
                        f"active session limit reached ({self._max_active_sessions})"
                    ),
                    retryable=True,
                )
            )
        self._starting_sessions += 1
        backend = self._backend or (
            pty_backend() if spec.mode == CommandMode.PTY else pipe_backend()
        )
        process_started = False
        try:
            handle = await backend.start(spec)
            process_started = True
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
        finally:
            if not process_started:
                self._starting_sessions -= 1

        session = Session(
            id=str(uuid4()),
            spec=spec,
            backend=backend,
            handle=handle,
            max_output_bytes=self._default_max_output_bytes
            if max_output_bytes is None
            else max_output_bytes,
            timeout_ms=timeout_ms,
            idle_timeout_ms=idle_timeout_ms,
            max_output_events=self._max_output_events,
            max_retained_events=self._max_retained_events,
            trace_id=trace_id or str(uuid4()),
            request_id=request_id,
            created_monotonic=requested_monotonic,
            started_monotonic=time.monotonic(),
            event_recorder=self._event_recorder,
            output_retention=output_retention,
        )
        self._sessions[session.id] = session
        self._starting_sessions -= 1
        self._started_sessions += 1
        session.transition(SessionState.ACCEPTED)
        await session.emit(LifecycleEventType.ACCEPTED, {"session_id": session.id})
        session.transition(SessionState.STARTING)
        session.transition(SessionState.RUNNING)
        await session.emit(
            LifecycleEventType.PROCESS_STARTED,
            {
                "pid": handle.pid,
                "mode": spec.mode.value,
                "process_tree": handle.process_tree,
            },
        )
        for reason in handle.degraded_reasons:
            await session.emit(
                LifecycleEventType.CAPABILITY_DEGRADED,
                {"capability": "process_tree", "reason": reason},
            )
        session.monitor_task = asyncio.create_task(self._monitor(session))
        log_event(
            logging.INFO,
            "session.started",
            session_id=session.id,
            trace_id=session.trace_id,
            pid=handle.pid,
            mode=spec.mode.value,
        )
        return session

    def get(self, session_id: str) -> Session:
        self._prune_completed_sessions()
        try:
            return self._sessions[session_id]
        except KeyError as err:
            if session_id in self._expired_session_ids:
                raise SharkRailError(
                    ExecutionError(
                        code=ErrorCode.SESSION_EXPIRED,
                        stage=ErrorStage.RUN,
                        message=f"Session expired: {session_id}",
                    ),
                    err,
                ) from err
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.SESSION_NOT_FOUND,
                    stage=ErrorStage.RUN,
                    message=f"Session not found: {session_id}",
                ),
                err,
            ) from err

    async def write(self, session_id: str, data: bytes) -> None:
        session = self.get(session_id)
        async with session.operation_lock:
            self._require_active(session_id)
            if len(data) > self._max_input_bytes:
                raise SharkRailError(
                    ExecutionError(
                        code=ErrorCode.RESOURCE_LIMITED,
                        stage=ErrorStage.RUN,
                        message=f"input exceeds per-write limit ({self._max_input_bytes} bytes)",
                    )
                )
            if session.input_bytes + len(data) > self._max_total_input_bytes:
                await session.emit(
                    LifecycleEventType.RESOURCE_LIMIT_HIT,
                    {
                        "resource": "total_input_bytes",
                        "limit": self._max_total_input_bytes,
                    },
                )
                raise SharkRailError(
                    ExecutionError(
                        code=ErrorCode.RESOURCE_LIMITED,
                        stage=ErrorStage.RUN,
                        message=(
                            "session input exceeds total limit "
                            f"({self._max_total_input_bytes} bytes)"
                        ),
                    )
                )
            await asyncio.wait_for(
                session.backend.write(session.handle, data),
                self._termination_timeout_ms / 1000,
            )
            self._total_input_bytes += len(data)
            session.input_bytes += len(data)

    async def close_stdin(self, session_id: str) -> None:
        session = self.get(session_id)
        async with session.operation_lock:
            self._require_active(session_id)
            await asyncio.wait_for(
                session.backend.close_stdin(session.handle),
                self._termination_timeout_ms / 1000,
            )

    async def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self.get(session_id)
        async with session.operation_lock:
            self._require_active(session_id)
            if not hasattr(session.backend, "resize") or not isinstance(
                session.handle, PtyProcessHandle
            ):
                raise SharkRailError(
                    ExecutionError(
                        code=ErrorCode.CAPABILITY_NOT_SUPPORTED,
                        stage=ErrorStage.RUN,
                        message="resize is only supported for PTY sessions",
                    )
                )
            await asyncio.wait_for(
                session.backend.resize(session.handle, cols, rows),
                self._termination_timeout_ms / 1000,
            )

    async def interrupt(self, session_id: str) -> None:
        session = self.get(session_id)
        async with session.operation_lock:
            self._require_active(session_id)
            await session.emit(
                LifecycleEventType.CANCELLATION_STEP, {"step": "interrupt"}
            )
            await asyncio.wait_for(
                session.backend.interrupt(session.handle),
                self._termination_timeout_ms / 1000,
            )

    async def cancel(
        self,
        session_id: str,
        policy: Optional[CancellationPolicy] = None,
    ) -> tuple[str, ...]:
        session = self.get(session_id)
        async with session.operation_lock:
            if session.cancellation_steps or session.state in {
                SessionState.COMPLETED,
                SessionState.FAILED,
            }:
                return session.cancellation_steps
            self._require_active(session_id)
            if session.state == SessionState.RUNNING:
                session.transition(SessionState.CANCELLING)
            session.completion_reason = CompletionReason.CANCELLED
            self._cancellation_count += 1
            cancellation_started = time.monotonic()

            async def report_step(step: CancellationStep) -> None:
                value = step.value
                session.cancellation_steps = (*session.cancellation_steps, value)
                await session.emit(
                    LifecycleEventType.CANCELLATION_STEP, {"step": value}
                )

            try:
                await cancel_process(
                    session.backend,
                    session.handle,
                    policy,
                    step_handler=report_step,
                )
            except TimeoutError as err:
                await session.emit(
                    LifecycleEventType.CANCELLATION_COMPLETED,
                    {
                        "success": False,
                        "steps": session.cancellation_steps,
                        "duration_ms": round(
                            (time.monotonic() - cancellation_started) * 1000,
                            3,
                        ),
                    },
                )
                raise SharkRailError(
                    ExecutionError(
                        code=ErrorCode.TERMINATION_FAILED,
                        stage=ErrorStage.RUN,
                        message=str(err),
                    ),
                    err,
                ) from err
            await session.emit(
                LifecycleEventType.CANCELLATION_COMPLETED,
                {
                    "success": True,
                    "steps": session.cancellation_steps,
                    "duration_ms": round(
                        (time.monotonic() - cancellation_started) * 1000,
                        3,
                    ),
                },
            )
            return session.cancellation_steps

    async def wait(
        self, session_id: str, timeout_ms: Optional[int] = None
    ) -> Optional[CommandResult]:
        session = self.get(session_id)
        if session.monitor_task is None:
            return session.result
        try:
            if timeout_ms is None:
                await asyncio.shield(session.monitor_task)
            else:
                await asyncio.wait_for(
                    asyncio.shield(session.monitor_task), timeout_ms / 1000
                )
        except asyncio.TimeoutError:
            return None
        return session.result

    async def events_after(
        self,
        session_id: str,
        cursor: int = 0,
        wait_ms: int = 0,
    ) -> tuple[LifecycleEvent, ...]:
        events, _, _ = await self.event_page(
            session_id,
            cursor=cursor,
            wait_ms=wait_ms,
            limit=self._max_event_page_size,
        )
        return events

    async def event_page(
        self,
        session_id: str,
        *,
        cursor: int = 0,
        wait_ms: int = 0,
        limit: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> tuple[tuple[LifecycleEvent, ...], int, bool]:
        session = self.get(session_id)
        if cursor < session.first_event_seq:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.EVENT_CURSOR_EXPIRED,
                    stage=ErrorStage.RUN,
                    message=(
                        f"event cursor {cursor} is older than retained cursor "
                        f"{session.first_event_seq}"
                    ),
                    retryable=False,
                    native={"first_cursor": session.first_event_seq},
                )
            )
        if cursor < 0 or cursor > session.next_event_seq:
            raise self._request_error("event cursor is out of range")
        if (
            cursor == session.next_event_seq
            and wait_ms > 0
            and session.state
            not in {
                SessionState.COMPLETED,
                SessionState.FAILED,
            }
        ):
            async with session.condition:
                try:
                    await asyncio.wait_for(
                        session.condition.wait_for(
                            lambda: (
                                session.next_event_seq > cursor
                                or session.state
                                in {SessionState.COMPLETED, SessionState.FAILED}
                            )
                        ),
                        wait_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    pass
        page_limit = self._max_event_page_size if limit is None else limit
        if page_limit <= 0 or page_limit > self._max_event_page_size:
            raise self._request_error(
                f"event page limit must be between 1 and {self._max_event_page_size}"
            )
        byte_limit = self._max_event_page_bytes if max_bytes is None else max_bytes
        selected: list[LifecycleEvent] = []
        selected_bytes = 0
        for event in session.events:
            if event.seq < cursor:
                continue
            event_bytes = len(
                json.dumps(
                    event.payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            )
            if (
                selected
                and byte_limit is not None
                and selected_bytes + event_bytes > byte_limit
            ):
                break
            selected.append(event)
            selected_bytes += event_bytes
            if len(selected) >= page_limit:
                break
        next_cursor = selected[-1].seq + 1 if selected else cursor
        return tuple(selected), next_cursor, next_cursor < session.next_event_seq

    async def dispose(self, session_id: str) -> None:
        if session_id in self._disposed_session_ids:
            return
        session = self.get(session_id)
        if session.state not in {
            SessionState.COMPLETED,
            SessionState.FAILED,
            SessionState.DISPOSED,
        }:
            try:
                await self.cancel(session_id, CancellationPolicy(skip_interrupt=True))
            except SharkRailError as err:
                if err.error.code != ErrorCode.INVALID_SESSION_STATE:
                    raise
            await self.wait(session_id, timeout_ms=self._shutdown_timeout_ms)
        try:
            await asyncio.wait_for(
                session.backend.dispose(session.handle),
                self._termination_timeout_ms / 1000,
            )
        except asyncio.TimeoutError as err:
            raise SharkRailError(
                ExecutionError(
                    code=ErrorCode.TERMINATION_FAILED,
                    stage=ErrorStage.DISPOSE,
                    message="backend disposal exceeded its deadline",
                ),
                err,
            ) from err
        if session.state != SessionState.DISPOSED:
            session.transition(SessionState.DISPOSED)
        self._sessions.pop(session_id, None)
        self._disposed_session_ids.append(session_id)

    async def shutdown(self) -> None:
        """Dispose every session when the owning transport shuts down."""
        tasks = [
            asyncio.create_task(self.dispose(session_id))
            for session_id in tuple(self._sessions)
        ]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                self._shutdown_timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def stats(self) -> dict[str, object]:
        self._prune_completed_sessions()
        states = Counter(session.state.value for session in self._sessions.values())
        active = self._starting_sessions + sum(
            count
            for state, count in states.items()
            if state not in {SessionState.COMPLETED.value, SessionState.FAILED.value}
        )
        return {
            "uptime_ms": round((time.monotonic() - self._created_monotonic) * 1000, 3),
            "sessions": {
                "active": active,
                "starting": self._starting_sessions,
                "retained": len(self._sessions),
                "started": self._started_sessions,
                "states": dict(states),
                "completed_by_reason": dict(self._completion_counts),
            },
            "errors_by_code": dict(self._error_counts),
            "io": {
                "input_bytes": self._total_input_bytes,
                "output_bytes": self._total_output_bytes,
                "retained_output_bytes": self._total_retained_output_bytes,
                "dropped_output_bytes": self._total_dropped_output_bytes,
            },
            "cancellations": self._cancellation_count,
            "event_recorder": (
                None
                if self._event_recorder is None
                else {
                    "truncated": self._event_recorder.truncated,
                    "last_error": self._event_recorder.last_error,
                }
            ),
        }

    def list_sessions(self) -> tuple[dict[str, object], ...]:
        self._prune_completed_sessions()
        return tuple(self.inspect(session.id) for session in self._sessions.values())

    def inspect(self, session_id: str) -> dict[str, object]:
        session = self.get(session_id)
        now = time.monotonic()
        return {
            "session_id": session.id,
            "trace_id": session.trace_id,
            "request_id": session.request_id,
            "state": session.state.value,
            "pid": session.handle.pid,
            "mode": session.spec.mode.value,
            "process_tree": session.handle.process_tree,
            "degraded_reasons": session.handle.degraded_reasons,
            "created_at": session.created_at,
            "duration_ms": self._duration_ms(session, now),
            "drain_duration_ms": self._drain_duration_ms(session, now),
            "last_output_age_ms": (
                None
                if session.last_output_monotonic is None
                else round((now - session.last_output_monotonic) * 1000, 3)
            ),
            "output_bytes": session.total_output_bytes,
            "input_bytes": session.input_bytes,
            "retained_output_bytes": len(session.stdout) + len(session.stderr),
            "dropped_output_bytes": session.truncated_output_bytes,
            "first_cursor": session.first_event_seq,
            "next_cursor": session.next_event_seq,
            "cancellation_steps": session.cancellation_steps,
            "idle_timeout_ms": session.idle_timeout_ms,
        }

    async def _monitor(self, session: Session) -> None:
        readers: list[asyncio.Task[None]] = []
        if hasattr(session.backend, "read") and isinstance(
            session.handle, PtyProcessHandle
        ):
            readers.append(
                asyncio.create_task(
                    self._read_pty(session, session.backend, session.handle)
                )
            )
        else:
            if session.handle.process.stdout is not None:
                readers.append(
                    asyncio.create_task(
                        self._read_pipe(
                            session, "stdout", session.handle.process.stdout
                        )
                    )
                )
            if session.handle.process.stderr is not None:
                readers.append(
                    asyncio.create_task(
                        self._read_pipe(
                            session, "stderr", session.handle.process.stderr
                        )
                    )
                )

        monitor_error: Optional[ExecutionError] = None
        disposed = False
        # asyncio's POSIX Process.wait() may not finish until subprocess pipe
        # transports reach EOF, even after returncode is available. Descendants
        # can inherit those pipes, so root exit detection must stay independent
        # from the bounded output-drain phase below.
        process_wait = asyncio.create_task(self._wait_for_process_exit(session.handle))
        try:
            timeout_reason = await self._wait_reason(session, process_wait)
        except Exception as err:  # noqa: BLE001 - backend boundary
            timeout_reason = None
            monitor_error = self._backend_error(err, ErrorStage.RUN)
        if timeout_reason is not None:
            try:
                session.completion_reason = timeout_reason
                await session.backend.kill_tree(session.handle)
                await asyncio.wait_for(
                    asyncio.shield(process_wait),
                    self._termination_timeout_ms / 1000,
                )
            except asyncio.TimeoutError:
                monitor_error = ExecutionError(
                    code=ErrorCode.TERMINATION_FAILED,
                    stage=ErrorStage.RUN,
                    message="process did not exit after forced termination",
                )
            except Exception as err:  # noqa: BLE001 - backend boundary
                monitor_error = self._backend_error(err, ErrorStage.RUN)

        if monitor_error is None:
            try:
                if session.state == SessionState.RUNNING:
                    session.transition(SessionState.EXITING)
                session.exited_monotonic = time.monotonic()
                session.transition(SessionState.DRAINING)
                session.drain_started_monotonic = time.monotonic()
                await session.emit(
                    LifecycleEventType.PROCESS_EXITED,
                    {"exit_code": session.handle.process.returncode},
                )
                reader_wait = asyncio.gather(*readers)
                try:
                    await asyncio.wait_for(
                        asyncio.shield(reader_wait),
                        self._drain_timeout_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    await session.emit(
                        LifecycleEventType.RESOURCE_LIMIT_HIT,
                        {"resource": "drain_time", "limit_ms": self._drain_timeout_ms},
                    )
                    await session.backend.kill_tree(session.handle)
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(reader_wait),
                            self._termination_timeout_ms / 1000,
                        )
                    except asyncio.TimeoutError:
                        pass
                    raise SharkRailError(
                        ExecutionError(
                            code=ErrorCode.DRAIN_TIMEOUT,
                            stage=ErrorStage.DRAIN,
                            message=f"output did not drain within {self._drain_timeout_ms} ms",
                        )
                    )
                await session.emit(
                    LifecycleEventType.SESSION_DRAINED,
                    {"output_bytes": session.total_output_bytes},
                )
            except SharkRailError as err:
                monitor_error = err.error
            except Exception as err:  # noqa: BLE001 - backend boundary
                monitor_error = self._backend_error(err, ErrorStage.DRAIN)

        try:
            await asyncio.wait_for(
                session.backend.dispose(session.handle),
                self._termination_timeout_ms / 1000,
            )
            disposed = True
        except asyncio.TimeoutError:
            if monitor_error is None:
                monitor_error = ExecutionError(
                    code=ErrorCode.TERMINATION_FAILED,
                    stage=ErrorStage.DISPOSE,
                    message="backend disposal exceeded its deadline",
                )
        except Exception as err:  # noqa: BLE001 - backend boundary
            if monitor_error is None:
                monitor_error = self._backend_error(err, ErrorStage.DISPOSE)
        finally:
            if not process_wait.done():
                process_wait.cancel()
                await asyncio.gather(process_wait, return_exceptions=True)
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)

        captured = capture_output(bytes(session.stdout), bytes(session.stderr), None)
        resource_hit = self._infer_resource_limit(session)
        if monitor_error is None and resource_hit is not None:
            await session.emit(LifecycleEventType.RESOURCE_LIMIT_HIT, resource_hit)
            monitor_error = ExecutionError(
                code=ErrorCode.RESOURCE_LIMITED,
                stage=ErrorStage.RUN,
                message=f"process reached the {resource_hit['resource']} limit",
                native={"exit_code": session.handle.process.returncode},
            )
        reason = session.completion_reason
        if monitor_error is not None:
            reason = (
                CompletionReason.RESOURCE_LIMITED
                if monitor_error.code
                in {ErrorCode.DRAIN_TIMEOUT, ErrorCode.RESOURCE_LIMITED}
                else CompletionReason.FAILED
            )
            if session.state not in {SessionState.FAILED, SessionState.COMPLETED}:
                session.transition(SessionState.FAILED)
            await session.emit(
                LifecycleEventType.SESSION_ERROR, monitor_error.to_dict()
            )
        elif reason is None:
            reason = (
                CompletionReason.SUCCESS
                if session.handle.process.returncode == 0
                else CompletionReason.FAILED
            )

        exit_code = session.handle.process.returncode
        if reason in {CompletionReason.TIMEOUT, CompletionReason.IDLE_TIMEOUT}:
            exit_code = 124
        elif monitor_error is not None and (exit_code is None or exit_code == 0):
            exit_code = 1
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
            timed_out=reason
            in {CompletionReason.TIMEOUT, CompletionReason.IDLE_TIMEOUT},
            error=monitor_error,
            duration_ms=self._duration_ms(session),
            drain_duration_ms=self._drain_duration_ms(session),
            stdout_bytes=bytes(session.stdout),
            stderr_bytes=bytes(session.stderr),
        )
        if monitor_error is None:
            session.transition(SessionState.COMPLETED)
        session.completed_at_monotonic = time.monotonic()
        await session.emit(
            LifecycleEventType.SESSION_COMPLETED,
            {
                "reason": reason.value,
                "exit_code": session.result.exit_code,
                "resources_disposed": disposed,
                "duration_ms": self._duration_ms(session),
                "drain_duration_ms": self._drain_duration_ms(session),
            },
        )
        self._completion_counts[reason.value] += 1
        if monitor_error is not None:
            self._error_counts[monitor_error.code.value] += 1
        self._total_output_bytes += session.total_output_bytes
        self._total_retained_output_bytes += captured.retained_bytes
        self._total_dropped_output_bytes += session.truncated_output_bytes
        log_event(
            logging.ERROR if monitor_error is not None else logging.INFO,
            "session.completed",
            session_id=session.id,
            trace_id=session.trace_id,
            reason=reason.value,
            exit_code=session.result.exit_code,
            duration_ms=self._duration_ms(session),
            drain_duration_ms=self._drain_duration_ms(session),
            error_code=monitor_error.code.value if monitor_error is not None else None,
        )
        observe_session(
            reason=reason.value,
            duration_ms=self._duration_ms(session),
            drain_duration_ms=self._drain_duration_ms(session),
            output_bytes=session.total_output_bytes,
            dropped_bytes=session.truncated_output_bytes,
        )
        self._prune_completed_sessions()

    async def _wait_reason(
        self,
        session: Session,
        process_wait: asyncio.Task[int],
    ) -> Optional[CompletionReason]:
        while True:
            now = time.monotonic()
            deadlines: list[tuple[float, CompletionReason]] = []
            if session.timeout_ms is not None:
                deadlines.append(
                    (
                        session.started_monotonic + session.timeout_ms / 1000,
                        CompletionReason.TIMEOUT,
                    )
                )
            if session.idle_timeout_ms is not None:
                last_activity = (
                    session.last_output_monotonic or session.started_monotonic
                )
                deadlines.append(
                    (
                        last_activity + session.idle_timeout_ms / 1000,
                        CompletionReason.IDLE_TIMEOUT,
                    )
                )
            if not deadlines:
                await asyncio.shield(process_wait)
                return None

            deadline, reason = min(deadlines, key=lambda item: item[0])
            try:
                await asyncio.wait_for(
                    asyncio.shield(process_wait),
                    max(0.0, deadline - now),
                )
                return None
            except asyncio.TimeoutError:
                current = time.monotonic()
                if reason == CompletionReason.TIMEOUT and current >= deadline:
                    return reason
                if reason == CompletionReason.IDLE_TIMEOUT:
                    last_activity = (
                        session.last_output_monotonic or session.started_monotonic
                    )
                    if current >= last_activity + (session.idle_timeout_ms or 0) / 1000:
                        return reason

    @staticmethod
    async def _wait_for_process_exit(handle: ProcessHandle) -> int:
        # PTY adapters own their output channel independently from asyncio's
        # subprocess pipe transports. In particular, pywinpty updates its
        # cached exit status from wait(), so polling returncode would never
        # observe completion for a short-lived ConPTY process.
        if isinstance(handle, PtyProcessHandle):
            return await handle.process.wait()
        while handle.process.returncode is None:
            await asyncio.sleep(0.01)
        return handle.process.returncode

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
        backend: Any,
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
    def _backend_error(error: Exception, stage: ErrorStage) -> ExecutionError:
        native: dict[str, object] = {"exception": type(error).__name__}
        if isinstance(error, OSError) and error.errno is not None:
            native["errno"] = error.errno
        return ExecutionError(
            code=ErrorCode.INTERNAL_ERROR,
            stage=stage,
            message=str(error) or type(error).__name__,
            native=native,
        )

    @staticmethod
    def _infer_resource_limit(session: Session) -> dict[str, object] | None:
        """Return only resource-limit outcomes the OS exposes unambiguously."""
        returncode = session.handle.process.returncode
        cpu_limit = session.spec.resources.cpu_time_seconds
        sigxcpu = getattr(signal, "SIGXCPU", None)
        if cpu_limit is not None and sigxcpu is not None and returncode == -sigxcpu:
            return {
                "resource": "cpu_time_seconds",
                "limit": cpu_limit,
                "attribution": "os_signal",
            }
        return None

    @staticmethod
    def _duration_ms(session: Session, now: Optional[float] = None) -> float:
        end = (
            session.exited_monotonic
            or session.completed_at_monotonic
            or now
            or time.monotonic()
        )
        return round(max(0.0, end - session.started_monotonic) * 1000, 3)

    @staticmethod
    def _drain_duration_ms(session: Session, now: Optional[float] = None) -> float:
        if session.drain_started_monotonic is None:
            return 0.0
        end = session.completed_at_monotonic or now or time.monotonic()
        return round(max(0.0, end - session.drain_started_monotonic) * 1000, 3)

    def _prune_completed_sessions(self) -> None:
        now = time.monotonic()
        completed = sorted(
            (
                session
                for session in self._sessions.values()
                if session.state in {SessionState.COMPLETED, SessionState.FAILED}
                and session.completed_at_monotonic is not None
            ),
            key=lambda session: session.completed_at_monotonic or 0,
        )
        expired = [
            session
            for session in completed
            if (now - (session.completed_at_monotonic or now)) * 1000
            >= self._completed_session_ttl_ms
        ]
        retained = [session for session in completed if session not in expired]
        overflow = max(0, len(retained) - self._max_completed_sessions)
        for session in [*expired, *retained[:overflow]]:
            if session.id not in self._sessions:
                continue
            session.transition(SessionState.DISPOSED)
            self._sessions.pop(session.id, None)
            self._expired_session_ids.append(session.id)

    @staticmethod
    def _request_error(message: str) -> SharkRailError:
        return SharkRailError(
            ExecutionError(
                code=ErrorCode.INVALID_REQUEST,
                stage=ErrorStage.VALIDATE,
                message=message,
            )
        )
