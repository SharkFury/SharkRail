"""Execution lifecycle primitives."""

from __future__ import annotations

from enum import Enum


class SessionState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"

class InvalidTransition(Exception):
    pass


@dataclass
class SessionLifecycle:
    state: SessionState = SessionState.PENDING

    def start(self) -> None:
        if self.state != SessionState.PENDING:
            raise InvalidTransition("Can only start from pending")
        self.state = SessionState.RUNNING

    def complete(self) -> None:
        if self.state != SessionState.RUNNING:
            raise InvalidTransition("Can only complete from running")
        self.state = SessionState.COMPLETED

    def cancel(self) -> None:
        if self.state != SessionState.RUNNING:
            raise InvalidTransition("Can only cancel from running")
        self.state = SessionState.CANCELED

    def fail(self) -> None:
        if self.state not in {SessionState.RUNNING, SessionState.PENDING}:
            raise InvalidTransition("Can only fail from pending or running")
        self.state = SessionState.FAILED
