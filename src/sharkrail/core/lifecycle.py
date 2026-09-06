"""Authoritative execution-session state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SessionState(str, Enum):
    CREATED = "created"
    PENDING = "created"  # noqa: PIE796 - backward-compatible enum alias
    ACCEPTED = "accepted"
    STARTING = "starting"
    RUNNING = "running"
    EXITING = "exiting"
    CANCELLING = "cancelling"
    DRAINING = "draining"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"  # Legacy terminal state retained for compatibility.
    DISPOSED = "disposed"


class InvalidTransition(Exception):
    pass


_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset(
        {SessionState.ACCEPTED, SessionState.RUNNING, SessionState.FAILED}
    ),
    SessionState.ACCEPTED: frozenset({SessionState.STARTING, SessionState.FAILED}),
    SessionState.STARTING: frozenset({SessionState.RUNNING, SessionState.FAILED}),
    SessionState.RUNNING: frozenset(
        {
            SessionState.EXITING,
            SessionState.CANCELLING,
            SessionState.DRAINING,
            SessionState.COMPLETED,
            SessionState.CANCELED,
            SessionState.FAILED,
        }
    ),
    SessionState.EXITING: frozenset({SessionState.DRAINING, SessionState.FAILED}),
    SessionState.CANCELLING: frozenset({SessionState.DRAINING, SessionState.FAILED}),
    SessionState.DRAINING: frozenset({SessionState.COMPLETED, SessionState.FAILED}),
    SessionState.COMPLETED: frozenset({SessionState.DISPOSED}),
    SessionState.FAILED: frozenset({SessionState.DISPOSED}),
    SessionState.CANCELED: frozenset({SessionState.DISPOSED}),
    SessionState.DISPOSED: frozenset(),
}


@dataclass
class SessionLifecycle:
    state: SessionState = SessionState.CREATED
    history: list[SessionState] = field(default_factory=lambda: [SessionState.CREATED])

    def transition(self, target: SessionState) -> None:
        if target not in _TRANSITIONS[self.state]:
            raise InvalidTransition(
                f"Cannot transition from {self.state.value} to {target.value}"
            )
        self.state = target
        self.history.append(target)

    def accept(self) -> None:
        self.transition(SessionState.ACCEPTED)

    def begin_start(self) -> None:
        self.transition(SessionState.STARTING)

    def start(self) -> None:
        self.transition(SessionState.RUNNING)

    def begin_exit(self) -> None:
        self.transition(SessionState.EXITING)

    def begin_drain(self) -> None:
        self.transition(SessionState.DRAINING)

    def complete(self) -> None:
        self.transition(SessionState.COMPLETED)

    def begin_cancel(self) -> None:
        self.transition(SessionState.CANCELLING)

    def cancel(self) -> None:
        self.transition(SessionState.CANCELED)

    def fail(self) -> None:
        self.transition(SessionState.FAILED)

    def dispose(self) -> None:
        self.transition(SessionState.DISPOSED)
